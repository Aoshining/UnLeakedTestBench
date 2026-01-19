set -e

export PREFIX=$(realpath "$(dirname "$0")/..")
MODELS=("1.5B_A1_0_noSFT" "1.5B_A1_0.25_noSFT" "1.5B_A1_0.5_noSFT" "1.5B_A1_0.75_noSFT" "1.5B_A1_1.0_noSFT")
for MODEL_NAME in "${MODELS[@]}"; do
    echo $PREFIX/ACG/testcase/rl/checkpoints/model_b/$MODEL_NAME
done > $PREFIX/UnLeakedTestBench/models.txt
cd $PREFIX/UnLeakedTestBench/src
python generate_cov_hf.py
source $PREFIX/ACG/code/rl/run/set_env.sh
python format.py
cd $PREFIX/UnLeakedTestBench
python Ray/main.py
export WANDB_MODE=offline
python print_results.py | tee results.txt
