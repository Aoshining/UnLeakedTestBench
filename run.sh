export PREFIX=$(realpath "$(dirname "$0")/../..")
cd $PREFIX/src
python generate_cov_hf.py
python format.py
cd $PREFIX
python Ray/main.py
python print_results.py | tee pytest_results.txt
python Ray/result_exporter.py | tee mut_results.txt
