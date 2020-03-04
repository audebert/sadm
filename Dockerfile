FROM gcr.io/halfr-cloud-stechec/sadm-base

# TMP FIX
RUN cd stechec2 && git pull && git checkout stats
RUN cd stechec2 && ./waf.py configure --prefix=/usr --with-games=prologin2019 && ./waf.py build install

# Copy sadm
COPY . /sadm
WORKDIR /sadm

# Setup Python package
RUN cd /sadm/python-lib && /env/bin/python setup.py install

# Add prologin config
COPY etc/prologin /etc/prologin
