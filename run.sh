export PREFIX=$(realpath $(dirname "$0"))
cd $PREFIX/src
python generate_cov_hf.py
python format.py
cd $PREFIX
python Ray/main.py
export WANDB_API_KEY=44059d01abbf3fa7cf233dbabff5be61bc6cd04a
wandb login
python print_results.py
