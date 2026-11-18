set -eu
data_file=/data/0_0.tigerbeetle
if [ ! -f "$data_file" ]; then
  /tigerbeetle format --cluster=0 --replica=0 --replica-count=1 --development "$data_file"
fi
exec /tigerbeetle start --addresses=0.0.0.0:3000 --development "$data_file"
