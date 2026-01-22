set -e

export PREFIX=$(realpath "$(dirname "$0")/..")
MODELS=("1.5B_A1_0_noSFT" "1.5B_A1_0.25_noSFT" "1.5B_A1_0.5_noSFT" "1.5B_A1_0.75_noSFT" "1.5B_A1_1.0_noSFT")
for MODEL_NAME in "${MODELS[@]}"; do
    echo $PREFIX/ACG/testcase/rl/checkpoints/model_b/$MODEL_NAME
done > $PREFIX/UnLeakedTestBench/models.txt
cd $PREFIX/UnLeakedTestBench/src
# 自动排队并行采样
# 遍历每一张显卡 (0 到 7)
for ((gpu_id=0; gpu_id<8; gpu_id++)); do
    (
        # 在这个子Shell中，只处理分配给当前GPU的任务
        # 循环步长为 8，实现轮询分配
        # 例如 GPU 0 处理索引: 0, 8, 16...
        for ((i=gpu_id; i<${#MODELS[@]}; i+=8)); do
            MODEL_NAME="${MODELS[$i]}"
            MODEL_PATH="$PREFIX/ACG/testcase/rl/checkpoints/model_b/$MODEL_NAME"
            
            echo "[GPU $gpu_id] Starting task: $MODEL_NAME"
            
            # 去掉了末尾的 &，让每张卡内部按顺序排队执行
            CUDA_VISIBLE_DEVICES=$gpu_id uv run generate_cov_hf.py --model $MODEL_PATH
                
            echo "[GPU $gpu_id] Finished task: $MODEL_NAME"
        done
    ) & # 将整个显卡的任务队列放入后台运行
done
# 等待所有显卡的队列执行完毕
wait
uv run generate_cov_hf.py
source $PREFIX/ACG/code/rl/run/set_env.sh
uv run format.py
cd $PREFIX/UnLeakedTestBench
uv run Ray/main.py
uv run print_results.py | tee results.txt
