FROM gcr.io/halfr-cloud-stechec/sadm-base

# Get the latest vesion of the code, or select a new branch
RUN git clone https://github.com/prologin/stechec2.git
RUN cd stechec2/games && git clone https://github.com/prologin/prologin2019.git
RUN cd stechec2 && ./waf.py configure --prefix=/usr --with-games=prologin2019 build install

# Copy sadm
COPY . /sadm
WORKDIR /sadm

# Setup Python package
RUN cd /sadm/python-lib && /env/bin/python setup.py install

# Add prologin config
COPY etc/prologin /etc/prologin
