"""
master.py

Drives a series of thread-scaling experiments with Bowtie, Bowtie 2 or HISAT.

Works for experiments that are:
1. MT    (one process, add threads)
2. MP+MT (fixed # threads / process, add processes)
3. MP    (MP+MT but with 1 thread / process)

Experiments scale the amount of input data with the total number of threads.
Input data is assumed to be pre-shuffled
"""

from __future__ import print_function
import os
import sys
import shutil
import argparse
import subprocess
import tempfile
import time
import datetime
import signal
import multiprocessing


join = os.path.join


def mkdir_quiet(dr):
    """ Create directories needed to ensure 'dr' exists; no complaining """
    import errno
    if not os.path.isdir(dr):
        try:
            os.makedirs(dr)
        except OSError as exception:
            if exception.errno != errno.EEXIST:
                raise


def tool_exe(tool):
    if tool == 'bowtie2' or tool == 'bowtie' or tool == 'hisat':
        return tool + '-align-s'
    elif tool == 'bwa':
        return 'bwa'
    else:
        raise RuntimeError('Unknown tool: "%s"' % tool)


def tool_ext(tool):
    if tool == 'bowtie2' or tool == 'hisat':
        return 'bt2'
    elif tool == 'bowtie':
        return 'ebwt'
    else:
        raise RuntimeError('Unknown tool: "%s"' % tool)


def make_tool_version(name, tool, preproc, build_dir):
    """ Builds target in specified clone """
    exe = tool_exe(tool)
    cmd = "make -e -C %s %s %s" % (build_dir, preproc, exe)
    print(cmd)
    ret = os.system(cmd)
    if ret != 0:
        raise RuntimeError('non-zero return from make for %s version "%s"' % (tool, name))


def install_tool_version(name, tool, url, branch, preproc, build_dir, make_tool=True):
    """ Clones appropriate branch """
    if len(branch) == 40 and branch.isalnum():
        cmd = "git clone %s -- %s && cd %s && git reset --hard %s" % (url, build_dir, build_dir, branch)
    else:
        cmd = "git clone %s -b %s -- %s" % (url, branch, build_dir)
    print(cmd)
    ret = os.system(cmd)
    if ret != 0:
        raise RuntimeError('non-zero return from git clone for %s version "%s"' % (tool, name))
    if make_tool:
        make_tool_version(name, tool, preproc, build_dir)


def get_configs(config_fn):
    """ Generator that parses and yields the lines of the config file """
    with open(config_fn) as fh:
        for ln in fh:
            toks = ln.split('\t')
            if toks[0] == 'name' and toks[1] == 'tool' and toks[2] == 'branch':
                continue
            if len(toks) == 0 or ln.startswith('#'):
                continue
            if len(toks) != 6:
                raise RuntimeError('Expected 6 tokens, got %d: %s' % (len(toks), ln))
            name, tool, branch, mp_mt, preproc, args = toks
            yield name, tool, branch, int(mp_mt), preproc, args.rstrip()


def verify_index(basename, tool):
    """ Check that all index files exist """
    def _ext_exists(ext):
        print('#  checking for "%s"' % (basename + ext), file=sys.stderr)
        return os.path.exists(basename + ext)
    if tool == 'bwa':
        ret = all(_ext_exists(x) for x in ['.amb', '.ann', '.pac', '.bwt', '.sa'])
    else:
        te = tool_ext(tool)
        ret = all(_ext_exists(x + te) for x in ['.1.', '.2.', '.3.', '.4.', '.rev.1.', '.rev.2.'])
        if ret and tool == 'hisat':
            return all(_ext_exists(x + te) for x in ['.5.', '.6.', '.rev.5.', '.rev.6.'])
    return ret


def verify_reads(fns):
    """ Check that files exist """
    for fn in fns:
        if fn is not None and (not os.path.exists(fn) or not os.path.isfile(fn)):
            raise RuntimeError('No such reads file as "%s"' % fn)
    return True


def wcl(fn):
    if fn.endswith('.gz'):
        return int(subprocess.check_output('gzip -dc ' + fn + ' | wc -l', shell=True).strip().split()[0])
    else:
        return int(subprocess.check_output('wc -l ' + fn, shell=True).strip().split()[0])


def slice_lab(i):
    ret = ''
    while i > 0:
        rem = i % 26
        remc = 'abcdefghijklmnopqrstuvwxyz'[rem]
        ret = remc + ret
        i /= 26
    while len(ret) < 3:
        ret = 'a' + ret
    assert len(ret) == 3
    return ret


def slice_all_fastq(reads_per, n, ifn, ofn, sanity=True, compress=False):
    assert 'block' not in ifn
    if ifn.endswith('.gz'):
        feed_cmd = 'gzip -dc ' + ifn
    else:
        feed_cmd = 'cat ' + ifn
    head_cmd = 'head -n %d' % (reads_per * n * 4)
    split_cmd = 'split -l %d -a 3 - %s' % (reads_per * 4, ofn)
    cmd = ' | '.join([feed_cmd, head_cmd, split_cmd])
    print(cmd)
    ret = os.system(cmd)
    if ret != 0:
        raise RuntimeError('Exitlevel %d from command "%s"' % (ret, cmd))
    if sanity:
        for i in range(n):
            fn = ofn + slice_lab(i)
            actual_nlines = wcl(fn)
            if actual_nlines != reads_per * 4:
                raise RuntimeError('Expected %d lines, found %d in "%s"' % (reads_per * 4, actual_nlines, fn))


def slice_fastq(begin, end, ifn, ofn, sanity=True):
    cmd = "sed -n '%d,%dp;%dq' < %s > %s" % (begin * 4 + 1, end * 4, end * 4 + 1, ifn, ofn)
    print(cmd)
    ret = os.system(cmd)
    if ret != 0:
        raise RuntimeError('Exitlevel %d from command "%s"' % (ret, cmd))
    if sanity:
        actual_nlines = wcl(ofn)
        if actual_nlines != (end - begin) * 4:
            raise RuntimeError('Expected %d lines, found %d in "%s"' % ((end - begin)*4, actual_nlines, ofn))


def prepare_reads(args, nthread, mp_mt, tmpdir, blocked=False):
    read_sets = []
    if mp_mt > 0:
        if blocked:
            raise RuntimeError('Unexpected combination of multiprocessing and blocked input')
        assert nthread % mp_mt == 0
        nprocess = int(nthread / mp_mt + 0.01)
        nreads_per_process = int((args.reads_per_thread * nthread) / nprocess + 0.01)
        prefs = list(map(lambda x: join(tmpdir, "%d_" % x), [1, 2]))
        slice_all_fastq(nreads_per_process, nprocess, args.m1, prefs[0])
        if args.m2 is not None:
            slice_all_fastq(nreads_per_process, nprocess, args.m2, prefs[1])
        for i in range(nprocess):
            fn0, fn1 = join(tmpdir, "1_" + slice_lab(i)), join(tmpdir, "2_" + slice_lab(i))
            if not os.path.exists(fn0):
                raise RuntimeError('Split failed to create file "%s"' % fn0)
            if args.m2 is not None:
                if not os.path.exists(fn1):
                    raise RuntimeError('Split failed to create file "%s"' % fn1)
                read_sets.append([fn0, fn1])
            else:
                read_sets.append([fn0])
        extra_fn = join(tmpdir, "1_" + slice_lab(nprocess))
        if os.path.exists(extra_fn):
            raise RuntimeError('Got one more output file than expected from split: "%s"' % extra_fn)
    else:
        rds_1 = join(tmpdir, "1.fq")
        rds_2 = join(tmpdir, "2.fq")
        nreads = args.reads_per_thread * nthread
        slice_fastq(0, nreads, args.m1b if blocked else args.m1, rds_1)
        if args.m2 is not None:
            slice_fastq(0, nreads, args.m2b if blocked else args.m2, rds_2)
            read_sets.append([rds_1, rds_2])
        else:
            read_sets.append([rds_1])
    return read_sets


repos = {'bowtie': 'https://github.com/BenLangmead/bowtie.git',
         'bowtie2': 'https://github.com/BenLangmead/bowtie2.git',
         'hisat': 'https://github.com/BenLangmead/hisat.git',
         'bwa': 'https://github.com/BenLangmead/bwa.git'}


def go(args):
    pe_str = 'pe' if args.m2 is not None else 'unp'

    # Set up temporary directory, used for holding read inputs and SAM output.
    # Strongly suggest that it be local, non-networked storage.
    print('# Setting up temporary directory', file=sys.stderr)
    tmpdir = args.tempdir
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp()
    if not os.path.exists(tmpdir):
        mkdir_quiet(tmpdir)
    if not os.path.isdir(tmpdir):
        raise RuntimeError('Temporary directory isn\'t a directory: "%s"' % tmpdir)
    else:
        os.system('rm -f ' + os.path.join(tmpdir, '1_???'))
        os.system('rm -f ' + os.path.join(tmpdir, '2_???'))

    if not os.path.exists(args.output_dir):
        print('# Creating output directory "%s"' % args.output_dir, file=sys.stderr)
        mkdir_quiet(args.output_dir)

    print('# Setting up binaries', file=sys.stderr)
    last_name, last_tool, last_branch, last_preproc, last_build_dir = '', '', '', '', ''
    npull, nbuild, ncopy, nlink = 0, 0, 0, 0
    for name, tool, branch, _, preproc, _ in get_configs(args.config):
        if args.preproc is not None:
            preproc += ' ' + args.preproc
        if name == 'name' and branch == 'branch':
            continue  # skip header line
        if len(last_tool) == 0:
            last_tool = tool
        assert tool == last_tool
        build, pull = False, False
        build_dir = join(args.build_dir, pe_str, name)
        if os.path.exists(build_dir) and args.force_builds:
            print('#   Removing existing "%s" subdir because of --force' % build_dir, file=sys.stderr)
            shutil.rmtree(build_dir)
            build = True
        elif os.path.exists(build_dir):
            pull = True
        elif not os.path.exists(build_dir):
            build = True

        if pull and args.pull:
            npull += 1
            print('#   Pulling "%s"' % name, file=sys.stderr)
            os.system('cd %s && git pull' % build_dir)
            make_tool_version(name, tool, preproc, build_dir)
        elif build and tool == last_tool and branch == last_branch and preproc == last_preproc:
            nlink += 1
            print('#   Linking "%s"' % name, file=sys.stderr)
            mkdir_quiet(os.path.dirname(build_dir))
            os.system('ln -s -f %s %s' % (last_name, build_dir))
        elif build and tool == last_tool and branch == last_branch:
            ncopy += 1
            print('#   Copying "%s"' % name, file=sys.stderr)
            mkdir_quiet(os.path.dirname(build_dir))
            os.system('cp -r %s %s' % (last_build_dir, build_dir))
            os.remove(os.path.join(build_dir, tool_exe(tool)))
            make_tool_version(name, tool, preproc, build_dir)
        elif build:
            nbuild += 1
            print('#   Building "%s"' % name, file=sys.stderr)
            install_tool_version(name, tool, repos[tool], branch, preproc, build_dir)
        last_name, last_tool, last_branch, last_preproc, last_build_dir = name, tool, branch, preproc, build_dir

    print('# Finished setting up binaries; built %d, pulled %d, copied %d, linked %d' %
          (nbuild, npull, ncopy, nlink), file=sys.stderr)

    series = list(map(int, args.nthread_series.split(',')))
    assert len(series) > 0
    print('#   series = %s' % str(series), file=sys.stderr)

    print('# Verifying reads', file=sys.stderr)
    verify_reads([args.m1, args.m2, args.m1b, args.m2b])

    if not args.no_count:
        print('# Counting total # reads', file=sys.stderr)
        nlines_tot = wcl(args.m1)
        nlines_tot_b = wcl(args.m1b)
        #if nlines_tot != nlines_tot_b:
        #    raise RuntimeError('Mismatch in # lines between unblocked (%d) and blocked (%d) inputs' % \
        #                       (nlines_tot, nlines_tot_b))

        nreads_tot = nlines_tot // 4
        print('# Count = %d' % nreads_tot, file=sys.stderr)

        nreads_needed = args.reads_per_thread * max(series)
        if nreads_needed > nreads_tot:
            raise RuntimeError('# reads required for biggest experiment (%d) exceeds number of input reads (%d)'
                               % (nreads_needed, nreads_tot))

    print('# Generating %scommands' % ('' if args.dry_run else 'and running '), file=sys.stderr)

    indexes_verified = set()

    read_set = None

    iostat_x = os.system("iostat --help 2>&1 | grep -q '\-x'") == 0

    # iterate over numbers of threads
    for nthreads in series:

        last_mp_mt, last_blocked = None, False

        # iterate over configurations
        for name, tool, branch, mp_mt, preproc, aligner_args in get_configs(args.config):
            build_dir = join(args.build_dir, pe_str, name)

            odir = join(args.output_dir, pe_str, name)
            if not os.path.exists(odir):
                print('#   Creating output directory "%s"' % odir, file=sys.stderr)
                mkdir_quiet(odir)

            redo = 1

            if tool not in indexes_verified:
                print('#   Verifying index for ' + tool, file=sys.stderr)
                verify_index(args.index, tool)
                indexes_verified.add(tool)
                redo = 2

            if mp_mt != 0 and (nthreads % mp_mt != 0):
                continue  # skip experiment if # threads isn't evenly divisible

            blocked = aligner_args is not None and 'block-bytes' in aligner_args
            if last_mp_mt is None or mp_mt != last_mp_mt or blocked != last_blocked:
                # Purge previous read set?
                print('#   Purging some old reads', file=sys.stderr)
                if read_set is not None:
                    for read_list in read_set:
                        for read_fn in read_list:
                            os.remove(read_fn)
                blocked_str = 'blocked' if blocked else 'unblocked'
                print('#   Preparing reads (%s) for nthreads=%d, mp_mt=%d' %
                      (blocked_str, nthreads, mp_mt), file=sys.stderr)
                mkdir_quiet(join(tmpdir, name, pe_str))
                read_set = prepare_reads(args, nthreads, mp_mt, join(tmpdir, name, pe_str), blocked=blocked)
                redo = 2
                last_mp_mt = mp_mt

            last_blocked = blocked

            nprocess = 1 if mp_mt == 0 else nthreads // mp_mt
            assert nprocess >= 1
            nthreads_per_process = nthreads if mp_mt == 0 else mp_mt
            print('# %s: nthreads=%d, nprocs=%d, threads per proc=%d' %
                  (name, nthreads, nprocess, nthreads_per_process), file=sys.stderr)

            for idx in range(redo):
                idx_rev = redo - idx
                print('# --- Attempt %d/%d ---' % (idx+1, redo))

                # Set up output files
                run_names = ['%s_%s_%d_%d_%d_%d' % (name, pe_str, mp_mt, i, nthreads, idx_rev) for i in range(nprocess)]
                run_name = run_names[0]
                stdout_ofns = ['/dev/null'] * nprocess
                stderr_ofns = ['/dev/null'] * nprocess
                sam_ofns = ['/dev/null'] * nprocess
                if idx_rev == 1:
                    stdout_ofns = [join(odir, '%s.out' % runname) for runname in run_names]
                    stderr_ofns = [join(odir, '%s.err' % runname) for runname in run_names]
                    if not args.sam_dev_null:
                        samdir = odir if args.sam_output_dir else tmpdir
                        for runname in run_names:
                            mkdir_quiet(join(samdir, name, pe_str, runname))
                        sam_ofns = [join(samdir, name, pe_str, runname, 'out.sam') for runname in run_names]

                def spawn_worker(cmd_list, ofn, efn):
                    def worker(done_val):
                        with open(ofn, 'wb') as ofh:
                            with open(efn, 'wb') as efh:
                                print(' '.join(cmd_list))
                                proc = subprocess.Popen(cmd_list, stdout=ofh, stderr=efh)
                                while proc.poll() is None:
                                    time.sleep(1)
                                    if done_val.value > 0:
                                        os.kill(proc.pid, signal.SIGTERM)
                                        break

                    return worker

                procs = []
                done_val = multiprocessing.Value('i', 0)
                if tool == 'bwa':
                    for i in range(nprocess):
                        cmd = ['%s/%s' % (build_dir, tool_exe(tool)), 'mem']
                        cmd.extend(['-t' , str(nthreads_per_process)])
                        if aligner_args is not None and len(aligner_args) > 0:
                            cmd.extend(aligner_args.split())
                        cmd.append(args.index)
                        cmd.append(read_set[i][0])
                        if args.m2 is not None:
                            cmd.append(read_set[i][1])
                        procs.append(multiprocessing.Process(target=spawn_worker(cmd, sam_ofns[i], stderr_ofns[i]), args=(done_val,)))
                else:
                    for i in range(nprocess):
                        cmd = ['%s/%s' % (build_dir, tool_exe(tool))]
                        cmd.extend(['-p', str(nthreads_per_process)])
                        if aligner_args is not None and len(aligner_args) > 0:
                            cmd.extend(aligner_args.split())
                        if tool == 'bowtie2' or tool == 'hisat':
                            cmd.append('-x')
                        cmd.append(args.index)
                        cmd.append('-t')
                        if mp_mt > 0:
                            cmd.append('--mm')
                        if args.m2 is not None:
                            cmd.extend(['-1', read_set[i][0]])
                            cmd.extend(['-2', read_set[i][1]])
                        elif tool == 'bowtie2' or tool == 'hisat':
                            cmd.extend(['-U', read_set[i][0]])
                        else:
                            cmd.append(read_set[i][0])

                        cmd.extend(['-S', sam_ofns[i]])
                        procs.append(multiprocessing.Process(target=spawn_worker(cmd, stdout_ofns[i], stderr_ofns[i]), args=(done_val,)))

                iostat_cmd = ['iostat']
                if iostat_x:
                    iostat_cmd.append('-x')
                iostat_cmd.append('2')
                iostat_fn = os.path.join(odir, run_name + '.iostat')

                if sys.platform == 'darwin':
                    top_cmd = 'top -l 0 -s 2'.split()
                else:
                    top_cmd = 'top -b -d 2'.split()
                top_fn = os.path.join(odir, run_name + '.top')

                with open(top_fn, 'w') as top_ofh:
                    with open(iostat_fn, 'w') as iostat_ofh:
                        iostat, top = None, None
                        if os.system('which iostat >/dev/null 2>/dev/null') == 0:
                            iostat = subprocess.Popen(iostat_cmd, stdout=iostat_ofh, stderr=iostat_ofh)
                        if os.system('which top >/dev/null 2>/dev/null') == 0:
                            top = subprocess.Popen(top_cmd, stdout=top_ofh, stderr=top_ofh)
                        print('#   Starting processes', file=sys.stderr)
                        ti = datetime.datetime.now()
                        for proc in procs:
                            proc.start()
                        exitlevels = []
                        for proc in procs:
                            proc.join(args.timeout)
                            if proc.is_alive():
                                print('#   Process still alive after %d seconds; terminating all processes' % args.timeout,
                                      file=sys.stderr)
                                done_val.value = 1
                                for p2 in procs:
                                    p2.join()
                                exitlevels.append(None)
                            else:
                                exitlevels.append(proc.exitcode)
                        if iostat is not None:
                            print('#   Killing iostat proc with pid %d' % iostat.pid, file=sys.stderr)
                            iostat.kill()
                        if top is not None:
                            print('#   Killing top proc with pid %d' % top.pid, file=sys.stderr)
                            top.kill()
                        delt = datetime.datetime.now() - ti
                print('#   All processes joined; took %f seconds' % delt.total_seconds(), file=sys.stderr)
                os.system('touch ' + os.path.join(odir, run_name + '.JOIN'))
                if any(map(lambda x: x is None, exitlevels)):
                    print('#   At least one subprocess timed out', file=sys.stderr)
                    os.system('touch ' + os.path.join(odir, run_name + '.TIME_OUT'))
                elif any(map(lambda x: x != 0, exitlevels)):
                    os.system('touch ' + os.path.join(odir, run_name + '.FAIL'))
                    if args.stop_on_fail:
                        raise RuntimeError('At least one subprocess exited with non-zero exit level. '
                                           'Exit levels: %s' % str(exitlevels))
                else:
                    os.system('touch ' + os.path.join(odir, run_name + '.SUCCEED'))

                if args.delete_sam:
                    print('#   Deleting SAM outputs', file=sys.stderr)
                    for sam_ofn in sam_ofns:
                        if sam_ofn != '/dev/null':
                            os.remove(sam_ofn)

    print('#   Purging some old reads', file=sys.stderr)
    if read_set is not None:
        for read_list in read_set:
            for read_fn in read_list:
                os.remove(read_fn)


if __name__ == '__main__':

    # Output-related options
    parser = argparse.ArgumentParser(description='Run a single series of thread-scaling experiments.')

    requiredNamed = parser.add_argument_group('required named arguments')
    requiredNamed.add_argument('--index', metavar='index_basename', type=str, required=True,
                        help='Path to indexes; omit final ".1.bt2" or ".1.ebwt".  Should usually be a human genome '
                             'index, with filenames like hg19.* or hg38.*')
    requiredNamed.add_argument('--config', metavar='pct,pct,...', type=str, required=True,
                        help='Specifies path to config file giving configuration short-names, tool names, branch '
                             'names, compilation macros, and command-line args.  (Provided master_config.tsv is '
                             'probably sufficient)')
    requiredNamed.add_argument('--output-dir', metavar='path', type=str, required=True,
                        help='Directory to put thread timings in.')
    requiredNamed.add_argument('--build-dir', metavar='path', type=str, default='build',
                        help='Directory to put git working copies & built binaries in.')
    requiredNamed.add_argument('--m1', metavar='path', type=str, required=True,
                        help='FASTQ file with mate 1s.  Will take subsets to construct inputs.')
    requiredNamed.add_argument('--m1b', metavar='path', type=str, required=True,
                        help='Blocked FASTQ file with mate 1s.  Will take subsets to construct inputs.')
    parser.add_argument('--m2', metavar='path', type=str,
                        help='FASTQ file with mate 2s.  Will take subsets to construct inputs.')
    parser.add_argument('--m2b', metavar='path', type=str,
                        help='Blocked FASTQ file with mate 1s.  Will take subsets to construct inputs.')
    parser.add_argument('--input-block-bytes', metavar='int', type=int, default=12288,
                        help='# bytes per input block')
    parser.add_argument('--input-reads-per-block', metavar='int', type=int, default=70,  # 44 for 100 bp reads
                        help='# reads in each input block')
    parser.add_argument('--timeout', metavar='int', type=int, default=1200,  # 20 minutes
                        help='time out after N seconds')
    parser.add_argument('--nthread-series', metavar='int,int,...', type=str, required=False,
                        help='Series of comma-separated ints giving the number of threads to use. '
                             'E.g. --nthread-series 10,20,30 will run separate experiments using '
                             '10, 20 and 30 threads respectively.  Deafult: just one experiment '
                             'using max # threads.')
    parser.add_argument('--repo', metavar='url', type=str, default=repos['bowtie'],
                        help='Path to repo for tool, cloned as needed (default: %s)' % repos['bowtie'])
    parser.add_argument('--tempdir', metavar='path', type=str, required=False,
                        help='Path for temporary files.  Used for reads files and output SAM.  Should be local, '
                             'non-networked storage.')
    parser.add_argument('--preproc', metavar='args', type=str, required=False,
                        help='Add preprocessing macros to be added to all build jobs.')
    parser.add_argument('--force-builds', action='store_const', const=True, default=False,
                        help='Overwrite binaries that already exist')
    parser.add_argument('--pull', action='store_const', const=True, default=False,
                        help='git pull into existing build directories (note: some might be tags rather than branches)')
    parser.add_argument('--dry-run', action='store_const', const=True, default=False,
                        help='Just verify that jobs can be run, then print out commands without running them; useful '
                             'for when you need to wrap the bowtie2 commands for profiling or other reasons')
    parser.add_argument('--sam-output-dir', action='store_const', const=True, default=False,
                        help='Put SAM output in the output directory rather than in the temporary directory.  '
                             'Usually we don\'t really care to examine the SAM output, so the default is reasonable.')
    parser.add_argument('--sam-dev-null', action='store_const', const=True, default=False,
                        help='Send SAM output directly to /dev/null.')
    parser.add_argument('--delete-sam', action='store_const', const=True, default=False,
                        help='Delete SAM file as soon as aligner finishes; useful if you need to avoid exhausting a '
                             'partition')
    parser.add_argument('--stop-on-fail', action='store_const', const=True, default=False,
                        help='Raise exception whenever any subprocess fails')
    parser.add_argument('--no-count', action='store_const', const=True, default=False,
                        help='Don\'t count reads at the beginning (can be slow)')
    parser.add_argument('--reads-per-thread', metavar='int', type=int, default=0,
                        help='set # of reads to align per thread/process directly, overrides --multiply-reads setting')

    go(parser.parse_args())
