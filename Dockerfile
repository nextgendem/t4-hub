FROM python:3.10.7-slim-buster

# docker build -t opendx/tslicerh .
#
# (if it complains with:
#  error checking context: no permission to read from '/home/rnebot/GoogleDrive/AA_OpenDx28/3dslicerhub/data/certificates/dhparam.pem'
#  --> sudo chown -R rnebot data/
# )

# NORMAL
RUN apt-get update && \
    apt-get -y install \
    gcc \
    git \
    curl \
    vim \
    build-essential \
    libpq-dev \
    wget \
    libcurl4-openssl-dev \
    libssl-dev \
    libgnutls28-dev \
    mime-support \
    libxml2-dev \
    libxslt-dev \
    zlib1g-dev \
    unzip \
    python-pytest \
    && apt-get clean

RUN curl -LO https://storage.googleapis.com/kubernetes-release/release/`curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt`/bin/linux/amd64/kubectl && \
    chmod +x ./kubectl && \
    mv ./kubectl /usr/local/bin/kubectl

# COMMON
RUN pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir git+https://github.com/Supervisor/supervisor gunicorn

WORKDIR /app

RUN mkdir -p /srv
VOLUME /srv

# needs to be set else Celery gives an error (because docker runs commands inside container as root)
ENV C_FORCE_ROOT=1

# NOTE: "requirements.txt" can be generated from scratch with "pipreqs --force ."
COPY requirements.txt /app
RUN pip3 install --no-cache-dir -r requirements.txt

# Supervisord
COPY supervisord.conf /etc/supervisord.conf
CMD ["supervisord", "-c", "/etc/supervisord.conf"]

EXPOSE 8080

COPY tsliceh_local.env /app/.env
COPY users /app/user
COPY proxy /app/proxy
COPY tsliceh /app/tsliceh
