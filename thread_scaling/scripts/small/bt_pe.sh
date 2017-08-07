#!/bin/bash -l

#SBATCH --job-name=TsSmBtPe
#SBATCH --output=.TsSmBtPe.out
#SBATCH --error=.TsSmBtPe.err
#SBATCH --partition=normal
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --time=24:00:00
#SBATCH -A TG-CIE170020

d=`dirname $PWD`
sh $d/bt.sh small pe
