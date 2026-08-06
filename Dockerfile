# For more information, please refer to https://aka.ms/vscode-docker-python
FROM python:3.12-slim

# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

# Install pip requirements
COPY requirements.txt .
# gcc is needed to build TgCrypto (kurigram[fast]), which ships source-only
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev \
 && python -m pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y gcc libc6-dev \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
#RUN apt update;apt install -yy apache2;sed -i 's/Listen 80/Listen 10000/' /etc/apache2/ports.conf
#EXPOSE 10000

WORKDIR /app
COPY . /app

# During debugging, this entry point will be overridden. For more information, please refer to https://aka.ms/vscode-docker-python-debug
CMD ["bash", "run.sh"]
