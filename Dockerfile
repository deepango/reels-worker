# Use the official FFmpeg image as the base
FROM jrottenberg/ffmpeg:7.1-ubuntu2404

# Run commands as root so we can install python
USER root

# Avoid timezone prompts during apt installations
ENV DEBIAN_FRONTEND=noninteractive

# Update apt and install Python 3 and pip
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Set up a working directory
WORKDIR /app

# Copy python dependencies file and install them
# We use --break-system-packages because we are in a container and don't strictly need a venv, 
# although a venv is safer. We'll just install globally for simplicity in the container.
COPY worker/requirements.txt /app/
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Copy the worker code itself
COPY worker/main.py /app/

# Clear the default ffmpeg entrypoint so we can run Python naturally
ENTRYPOINT []

# Set the start command to run the python queue consumer
CMD ["python3", "-u", "main.py"]
