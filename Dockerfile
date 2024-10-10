FROM transformer4:latest
USER root
ARG USER
ARG DAV_PORT

ENV USERNAME ${USER}
ENV DV_PORT ${DAV_PORT}
ENV STARTUPDIR=/dockerstartup
RUN echo "hola" > /home/kasm-user/a


# Crear usuario
RUN useradd -ms /bin/bash ${USER}
RUN echo "${USERNAME}:vncpassword" | chpasswd
# Configurar VNC server
RUN echo "${USER}" | openssl passwd -crypt -stdin > /home/kasm-user/.kasmpasswd && \
    chown -R ${USER}:${USER} /home/kasm-user/.kasmpasswd && \
    chmod 600 /home/kasm-user/.kasmpasswd
	
# Custom startup script
COPY ./easydav_restart.sh $STARTUPDIR/easydav_restart.sh
RUN awk "NR==FNR{val=${DAV_PORT}; next} {sub(/8085/, val); print}" /opt/easydav/webdav.py /opt/easydav/webdav.py > /opt/easydav/webdav2.py
RUN mv /opt/easydav/webdav2.py /opt/easydav/webdav.py
RUN chmod 755 $STARTUPDIR/easydav_restart.sh

USER vncuser