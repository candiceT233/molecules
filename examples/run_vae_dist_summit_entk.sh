#!/bin/bash

# -i {sparse_matrix_path} -o ./ --model_id {cvae_dir} -f sparse-concat -t resnet --dim1 168 --dim2 168 -d 21 --amp --distributed -b {batch_size} -e {epoch} -S 3
data_dir=${1}
output_dir=${2}
model_id=${3}
cm_format=${4}
model_type=${5}
height=${6}
width=${7}
dim=${8}
amp=${9}
distributed=${10}
batch_size=${11}
epoch=${12}
sample_interval=${13}
optimizer=${14}
checkpoint=${15}

if [ "$distributed" == "distributed" ]
then
	distributed='--distributed'
else
	distributed=''
fi

if [ "$amp" == "amp" ]
then
	amp="--amp"
else
	amp=""
fi

if [ "$checkpoint" != "" ]
then
	checkpoint="-c $checkpoint"
else
	checkpoint=""
fi

# create output dir
#mkdir -p ${output_dir}

conda_path='/gpfs/alpine/proj-shared/med110/atrifan/scripts/pytorch-1.6.0_cudnn-8.0.2.39_nccl-2.7.8-1_static_mlperf'
script_path='/gpfs/alpine/proj-shared/med110/hrlee/git/braceal/molecules/examples/example_vae.py'

# important variables
export WORLD_SIZE=${OMPI_COMM_WORLD_SIZE}
export RANK=${OMPI_COMM_WORLD_RANK}
export LOCAL_RANK=${OMPI_COMM_WORLD_LOCAL_RANK}
export MASTER_PORT=29500
export MASTER_ADDR=$(cat ${LSB_DJOB_HOSTFILE} | uniq | sort | grep -v batch | head -n1)
export LC_ALL=en_US.utf-8
export LANG=en_US.utf-8
export WANDB_MODE=dryrun

# determine gpu
enc_gpu=$(( ${LOCAL_RANK} ))
dec_gpu=$(( ${LOCAL_RANK} )) #+ 3 ))


#echo "REPORT: rank:${RANK}, local_rank:${LOCAL_RANK} enc:${enc_gpu} dec:${dec_gpu}"

# launch code
cmd="${conda_path}/bin/python -u ${script_path} \
       -i ${data_dir} \
       -o ${output_dir} \
       ${amp} ${distributed} \
       --model_prefix ${model_id} \
       -f ${cm_format} \
       -t ${model_type} \
       -e ${epoch} \
       -b ${batch_size} \
       -E ${enc_gpu} -D ${dec_gpu} \
       -opt ${optimizer} ${checkpoint} \
       -S ${sample_interval} \
       -ti $(($epoch+1)) \
       --dim1 ${height} --dim2 ${width} -d ${dim}
       --num_data_workers 4" # -ndw
echo ${cmd}
($cmd)
