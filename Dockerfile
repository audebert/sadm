FROM archlinux

# Setup prologin.org repository
ADD https://repo.prologin.org/prologin.pub prologin.pub
# TODO(halfr): add prologin.conf to repo.prologin.org
COPY arch/prologin.conf /
RUN cat /prologin.conf >> /etc/pacman.conf && pacman-key --init && pacman-key --add prologin.pub && pacman-key --lsign-key prologin

# Arch Linux setup
RUN pacman -Syu --noconfirm --needed base-devel python-virtualenv postgresql-libs git isolate-git stechec2 prologin2019 && pacman -Scc --noconfirm

# Setup venv
RUN virtualenv -p python3 /env
ENV PATH /env/bin:$PATH

# Copy sadm
COPY . /sadm
WORKDIR /sadm

# Setup Python package
RUN /env/bin/pip install --upgrade pip && /env/bin/pip install -r /sadm/requirements.txt
RUN cd /sadm/python-lib && /env/bin/python setup.py install

# Add prologin config
COPY etc/prologin /etc/prologin
