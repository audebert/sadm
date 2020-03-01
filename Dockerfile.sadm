FROM gcr.io/halfr-cloud-stechec/sadm-base

# Copy sadm
COPY . /sadm
WORKDIR /sadm

# Setup Python package
RUN cd /sadm/python-lib && /env/bin/python setup.py install

# Add prologin config
COPY etc/prologin /etc/prologin
