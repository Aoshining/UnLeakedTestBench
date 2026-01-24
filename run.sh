set -e

export PREFIX=$(realpath "$(dirname "$0")/..")
MODELS=("1.5B_A1_0.5_noSFT_nocode" "3B_A1_0.5_noSFT" "3B_SP" "7B_SP")
for MODEL_NAME in "${MODELS[@]}"; do
    if [[ $MODEL_NAME != *"SP"* ]]; then
        echo $PREFIX/ACG/testcase/rl/checkpoints/model_b/$MODEL_NAME
    else
        echo $PREFIX/ACG/code/rl/checkpoints/model_a/$MODEL_NAME
    fi
done > $PREFIX/UnLeakedTestBench/models.txt
cd $PREFIX/UnLeakedTestBench/src
uv run generate_cov_hf.py
source $PREFIX/ACG/code/rl/run/set_env.sh
uv run format.py
cd $PREFIX/UnLeakedTestBench
uv run Ray/main.py
uv run print_results.py | tee results.txt
