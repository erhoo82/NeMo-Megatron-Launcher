import sys
import os
import subprocess
import glob

import hydra
import omegaconf


def create_slurm_file(
        new_script_path,
        convert_cmd,
        job_name,
        flags="",
        dependency=None,
        time="04:00:00",
        exclusive=True,
        mem=0,
        overcommit=True,
        nodes=1,
        ntasks_per_node=1,
        gpus_per_task=1,
        partition="batch",
        account=None,
):
    with open(new_script_path, "w") as f:
        f.writelines("#!/bin/bash\n")
        f.writelines(f"#SBATCH --nodes={nodes}\n")
        f.writelines(f"#SBATCH --ntasks-per-node={ntasks_per_node}\n")
        if gpus_per_task is not None:
            f.writelines(f"#SBATCH --gpus-per-task={gpus_per_task}\n")
        if dependency is not None:
            if dependency != "singleton":
                dependency = f"afterany:{dependency}"
            f.writelines(f"#SBATCH --dependency={dependency}\n")
        f.writelines(f"#SBATCH -p {partition}\n")
        if account is not None:
            f.writelines(f"#SBATCH -A {account}\n")
        f.writelines(f"#SBATCH --job-name={job_name}\n")
        f.writelines(f"#SBATCH --mem={mem}\n")
        if exclusive:
            f.writelines("#SBATCH --exclusive\n")
        if overcommit:
            f.writelines("#SBATCH --overcommit\n")
        f.writelines(f"#SBATCH --time={time}\n\n")
        f.writelines(f'srun {flags} --ntasks={ntasks_per_node} sh -c "{convert_cmd}"\n\n')
        f.writelines("set +x\n")


def run_evaluation(cfg, dependency=None):
    # Read config
    bignlp_path = cfg.get("bignlp_path")
    container = cfg.get("container")
    container_mounts = cfg.get("container_mounts")
    convert_cfg = cfg.get("conversion")
    run_cfg = convert_cfg.get("run")
    model_cfg = convert_cfg.get("model")

    # SLURM parameters
    slurm_cfg = convert_cfg.get("slurm")
    partition = slurm_cfg.get("partition")
    account = slurm_cfg.get("account")
    time_limit = slurm_cfg.get("time_limit")
    nodes = slurm_cfg.get("nodes")
    exclusive = slurm_cfg.get("exclusive")
    mem = slurm_cfg.get("mem")
    overcommit = slurm_cfg.get("overcommit")
    ntasks_per_node = slurm_cfg.get("ntasks_per_node")
    gpus_per_task = slurm_cfg.get("gpus_per_task")
    if dependency is None:
        dependency = slurm_cfg.get("dependency")
    job_name = slurm_cfg.get("job_name")

    # Model parameters
    checkpoint_folder = model_cfg.get("checkpoint_folder")
    checkpoint_name = model_cfg.get("checkpoint_name")
    tensor_model_parallel_size = model_cfg.get("tensor_model_parallel_size")

    if tensor_model_parallel_size > 1:
        checkpoint = os.path.join(checkpoint_folder, "mp_rank_00", checkpoint_name)
    else:
        checkpoint = os.path.join(checkpoint_folder, checkpoint_name)
    checkpoint_list = glob.glob(checkpoint)
    if len(checkpoint_list) > 1:
        raise ValueError("Too many checkpoints fit the checkpoint name pattern in convert.yaml.")
    if len(checkpoint_list) == 0:
        raise ValueError("No checkpoint found with the checkpoint name pattern in convert.yaml.")
    checkpoint_name = os.path.basename(checkpoint_list[0])

    # Run parameters
    name = run_cfg.get("name")
    nemo_file_name = run_cfg.get("nemo_file_name")
    log_dir = os.path.join(bignlp_path, run_cfg.get("output_path"), name)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    nemo_file_path = os.path.join(log_dir, nemo_file_name)

    # Process container-mounts.
    mounts_str = f"{bignlp_path}:{bignlp_path}"
    if container_mounts is not None:
        assert isinstance(container_mounts, omegaconf.listconfig.ListConfig), "container_mounts must be a list."
        for mount in container_mounts:
            if mount is not None and isinstance(mount, str):
                mounts_str += f",{mount}:{mount}"

    flags = (
        f"--no-container-mount-home "
        f"--container-image {container} "
        f"--container-mounts {mounts_str} "
        f"-o {log_dir}/{name}-%j.log "
        f"-e {log_dir}/{name}-%j.error "
    )

    new_script_path = os.path.join(bignlp_path, "conversion_scripts/convert_script.sh")
    code_path = "/opt/bignlp/NeMo/examples/nlp/language_modeling/megatron_gpt_ckpt_to_nemo.py"
    convert_cmd = f"python -u {code_path} " \
                  f"--checkpoint_folder {checkpoint_folder} " \
                  f"--checkpoint_name {checkpoint_name} " \
                  f"--nemo_file_path {nemo_file_path} " \
                  f"--tensor_model_parallel_size {tensor_model_parallel_size} "

    create_slurm_file(
        new_script_path=new_script_path,
        convert_cmd=convert_cmd,
        job_name=job_name,
        flags=flags,
        dependency=dependency,
        exclusive=exclusive,
        mem=mem,
        overcommit=overcommit,
        time=time_limit,
        nodes=nodes,
        ntasks_per_node=ntasks_per_node,
        gpus_per_task=gpus_per_task,
        partition=partition,
        account=account,
    )
    job_id = subprocess.check_output(
        [f"sbatch --parsable {new_script_path}"], shell=True
    )
    dependency = job_id.decode("utf-8")
    print(f"Submitted Evaluation script with job id: {dependency}")
    return dependency
