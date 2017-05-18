#!/bin/bash

module load git
export LIBRARY_PATH=/work/04620/cwilks/tbb_gcc5.4_lib:$LIBRARY_PATH
export LD_LIBRARY_PATH=/work/04620/cwilks/tbb_gcc5.4_lib:$LD_LIBRARY_PATH
export CPATH=/work/04620/cwilks/tbb2017_20161128oss/include:$CPATH
export LIBS='-lpthread -ltbb -ltbbmalloc -ltbbmalloc_proxy'

export ROOT1=/work/04620/cwilks/data
export ROOT2=/tmp
export INDEX_ROOT=/dev/shm
export BT2_INDEX=$INDEX_ROOT
export HISAT_INDEX=$INDEX_ROOT
rsync -av $ROOT1/hg19* $INDEX_ROOT/
rsync -av $ROOT1/ERR050082_1.fastq.shuffled2.fq.block $ROOT2/
rsync -av $ROOT1/ERR050082_2.fastq.shuffled2.fq.block $ROOT2/

export BT2_READS=$ROOT2/ERR050082_1.fastq.shuffled2.fq.block
export BT2_READS_1=$ROOT2/ERR050082_1.fastq.shuffled2.fq.block
export BT2_READS_2=$ROOT2/ERR050082_2.fastq.shuffled2.fq.block

CONFIG=bt2_pub.tsv
CONFIG_MP=bt2_pub_mp.tsv
CONFIG_MP2=bt2_pub_mp2.tsv

#run MP+MT single and paired
./run_mp_mt_bt2.sh > run_bt2_mp_mt 2>&1

#run BWA single and paired
./run_bwa.sh ${1} > bwa_run.run 2>&1

#single
python ./master.py --reads-per-thread 12500 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series 1,4,8,12,16,17,34,51,68,85,100,102,119,136,150,153,170,200,204,221,238,255,272 --config ${CONFIG} --multiply-reads 8 --reads-per-batch 32 --paired-mode 2 --no-no-io-reads

#single MP
python ./master.py --multiprocess 12500 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series 1,4,8,12,16,17,34,51,68,85,100,102,119,136,150,153,170,200,204,221,238,255,272 --config ${CONFIG_MP} --multiply-reads 8 --reads-per-batch 32 --paired-mode 2 --no-no-io-reads


#paired
python ./master.py --reads-per-thread 18000 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series 1,4,8,12,16,17,34,51,68,85,100,102,119,136,150,153,170,200,204,221,238,255,272 --config ${CONFIG} --multiply-reads 6 --reads-per-batch 32 --paired-mode 3 --no-no-io-reads

#paired MP
python ./master.py --multiprocess 18000 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series 1,4,8,12,16,17,34,51,68,85,100,102,119,136,150,153,170,200,204,221,238,255,272 --config ${CONFIG_MP} --multiply-reads 6 --reads-per-batch 32 --paired-mode 3 --no-no-io-reads

#single MP w/ updated memory/output code
python ./master.py --multiprocess 12500 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series 1,4,8,12,16,17,34,51,68,85,100,102,119,136,150,153,170,200,204,221,238,255,272 --config ${CONFIG_MP2} --multiply-reads 8 --reads-per-batch 32 --paired-mode 2 --no-no-io-reads

