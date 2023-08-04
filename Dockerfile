ARG PYTHON_VERSION=3.11
FROM docker.io/library/python:${PYTHON_VERSION} as base

# Install OS packages
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
  && apt-get -y install --no-install-recommends \
  default-mysql-server \
  && rm -rf /var/lib/apt/lists/*

# install Python libraries
COPY requirements.txt /tmp/pip-tmp/
RUN pip3 --disable-pip-version-check --no-cache-dir install -r /tmp/pip-tmp/requirements.txt \
  && rm -rf /tmp/pip-tmp

##################################################

FROM base as builder

# build and install WeeWX to /app/
COPY . /builder/
RUN cd /builder/ \
  && sed -i 's|^home = .*$|home = /app|' ./setup.cfg \
  && python3 ./setup.py build \
  && python3 ./setup.py install --no-prompt
# && echo 'export PATH=/app/bin/:$PATH' > /etc/profile.d/add-app-bin.sh \
# && chmod 755 /etc/profile.d/add-app-bin.sh


##################################################

FROM base

# pull in built application
COPY --from=builder /app/ /app/

# Add program to PATH
RUN echo 'export PATH=/app/bin/:$PATH' > /etc/profile.d/add-app-bin.sh \
  && chmod 755 /etc/profile.d/add-app-bin.sh

RUN ls -halF . > /build_context.txt
