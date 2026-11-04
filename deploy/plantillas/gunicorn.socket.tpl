[Unit]
Description=gunicorn socket __CLIENTE__

[Socket]
ListenStream=__SOCKET__

[Install]
WantedBy=sockets.target
