# docker build -t opendx/nginx:latest ./proxy
FROM nginx:stable

# Copy custom Nginx configuration
COPY ./nginx.conf /etc/nginx/nginx.conf

# Ensure logs are streamed to stdout and stderr
RUN ln -sf /dev/stdout /var/log/nginx/access2.log \
    && ln -sf /dev/stderr /var/log/nginx/error2.log
