# Use the official Python image
FROM python:3.10-slim

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies required by OpenCV
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements.txt file into the container
COPY requirements.txt ./ 

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy the rest of the app's files into the container
COPY . .

# Expose the app's port
EXPOSE 8004

# Set the environment variable for Flask
ENV FLASK_APP=final.py
ENV FLASK_RUN_HOST=0.0.0.0
ENV FLASK_RUN_PORT=8004

# Start the Flask application using gunicorn
CMD ["flask", "run"]
