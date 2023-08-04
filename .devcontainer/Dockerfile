ARG PYTHON_VERSION=3.11
FROM mcr.microsoft.com/devcontainers/python:1-${PYTHON_VERSION}-bullseye

# # create weewx user
# ARG USERNAME=weewx
# RUN

# USER $USERNAME

# Install OS packages
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
  && apt-get -y install --no-install-recommends default-mysql-server

# install Python libraries
COPY requirements.txt /tmp/pip-tmp/
RUN pip3 --disable-pip-version-check --no-cache-dir install -r /tmp/pip-tmp/requirements.txt \
  && rm -rf /tmp/pip-tmp

# set root password
RUN echo 'root:rootpassword' | chpasswd

# build and install WeeWX to /app/
# RUN sed 's|^home = .*$|home = /app|' setup.cfg \
#   && python3 ./setup.py build \
#   && python3 ./setup.py install \
#   && echo 'export PATH=/app/bin/:$PATH' > /etc/profile.d/add-app-bin.sh \
#   && chmod 755 /etc/profile.d/add-app-bin.sh

# build and install WeeWX to /home/weewx
# RUN python3 ./setup.py build \
#   && python3 ./setup.py install

RUN ls -halF . > /build_context.txt
