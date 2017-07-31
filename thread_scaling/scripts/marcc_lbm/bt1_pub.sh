#!/bin/bash

# ','.join(map(str, sorted(set([i for i in range(12, 112+1, 12)] + [i for i in range(8, 112+1, 8)] + [1]))))
THREAD_SERIES="1,8,12,16,24,32,36,40,48,56,60,64,72,80,84,88,96,104,108,112"

module load git
export LD_LIBRARY_PATH=/home-1/cwilks3@jhu.edu/tbb2017_20161128oss.bin/lib/intel64/gcc4.1:$LD_LIBRARY_PATH
export LIBRARY_PATH=/home-1/cwilks3@jhu.edu/tbb2017_20161128oss.bin/lib/intel64/gcc4.1:$LIBRARY_PATH
export CPATH=/home-1/cwilks3@jhu.edu/tbb2017_20161128oss.bin/include:$CPATH
export LIBS="-lpthread -ltbb -ltbbmalloc -ltbbmalloc_proxy"

export INDEX_ROOT=/storage/indexes

export BT2_INDEX=$INDEX_ROOT
export HISAT_INDEX=$INDEX_ROOT


export ROOT1=/home-1/cwilks3@jhu.edu/scratch
export ROOT2=/local
rsync -av $ROOT1/ERR050082_1.fastq.shuffled2_extended.fq.block  $ROOT2/
rsync -av $ROOT1/ERR050082_2.fastq.shuffled2.fq.block  $ROOT2/


#export BT2_READS=$ROOT2/ERR050082_1.fastq.shuffled.fq.block 
#use the extended version to have enough reads for bowtie
#this is the whole ~42m reads from the original catted
#with the first 30m reads again at the end to get ~72m reads
export BT2_READS=$ROOT2/ERR050082_1.fastq.shuffled2_extended.fq.block
export BT2_READS_1=$ROOT2/ERR050082_1.fastq.shuffled2_extended.fq.block
export BT2_READS_2=$ROOT2/ERR050082_2.fastq.shuffled2.fq.block 

CONFIG=./experiments/bt1_pub.tsv
CONFIG_MP=./experiments/bt1_pub_mp.tsv

if [ ! -d "${1}/mp_mt_bt1" ]; then
	mkdir -p ${1}/mp_mt_bt1
fi

./experiments/marcc_lbm/run_mp_mt_bt1.sh ${1}/mp_mt_bt1 > run_mp_mt_bt1.run 2>&1

#single
python ./master.py --reads-per-thread 450000 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series $THREAD_SERIES --config ${CONFIG} --multiply-reads 60 --reads-per-batch 32 --paired-mode 2 --no-no-io-reads --shorten-reads

#single MP
python ./master.py --multiprocess 450000 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series $THREAD_SERIES --config ${CONFIG_MP} --multiply-reads 60 --reads-per-batch 32 --paired-mode 2 --no-no-io-reads --shorten-reads

#paired
python ./master.py --reads-per-thread 180000 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series $THREAD_SERIES --config ${CONFIG} --multiply-reads 6 --reads-per-batch 32 --paired-mode 3 --no-no-io-reads --shorten-reads

#paired MP
python ./master.py --multiprocess 180000 --index $BT2_INDEX/hg19 --hisat-index $HISAT_INDEX/hg19_hisat --U $BT2_READS --m1 $BT2_READS_1 --m2 $BT2_READS_2 --sensitivities s --sam-dev-null --tempdir $ROOT2 --output-dir ${1} --nthread-series $THREAD_SERIES --config ${CONFIG_MP} --multiply-reads 6 --reads-per-batch 32 --paired-mode 3 --no-no-io-reads --shorten-reads