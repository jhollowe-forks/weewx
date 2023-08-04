#!/usr/bin/env sh

# python3 ./setup.py build
sudo python3 ./setup.py install --no-prompt

export MYSQL_PWD=rootpassword
make test-setup
