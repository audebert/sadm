FROM archlinux

# Arch Linux setup
RUN pacman -Syu --noconfirm --needed base-devel python-virtualenv postgresql-libs git && pacman -Scc --noconfirm

# Setup venv
RUN /usr/bin/virtualenv -p python3 /env
ENV PATH /env/bin:$PATH

# Setup requirements
ADD requirements.txt /app/requirements.txt
RUN /env/bin/pip install --upgrade pip && /env/bin/pip install -r /app/requirements.txt

# Add app config
ADD etc/prologin /etc/prologin

# Install app
ADD python-lib /app
WORKDIR /app
RUN /env/bin/python setup.py install
