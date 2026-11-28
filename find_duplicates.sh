#/bin/bash

DIR=$1

num=0
for f in $DIR/*; do
  for g in $DIR/*; do
    u=$(compare -metric ncc "$f" "$g" NULL: 2>&1)
    v=$(echo "1000*$u" | bc | awk -F. '{print $1}' | sed 's/^[0]*//g')
    [[ $v -ge 1000 ]] && [[ "$f" != "$g" ]] && echo "rm -f $(basename "$g")"
  done
done
