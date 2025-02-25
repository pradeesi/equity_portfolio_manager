# Use a slim Python 3.9 base image
FROM python:3.9-slim-buster

# Set environment variable for production
ENV FLASK_ENV=production

# Set working directory inside the container
WORKDIR /app

# Copy requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Expose the port Flask will listen on
#EXPOSE 5000 # remove or comment this line 

# Set the command to run the Flask app
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app", "--workers", "3"]
