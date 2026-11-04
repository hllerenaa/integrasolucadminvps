[Unit]
Description=gunicorn daemon __CLIENTE__
Requires=__CLIENTE__.socket
After=network.target

[Service]
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=5
Restart=on-failure
RestartSec=10
User=root
Group=www-data
WorkingDirectory=__RUTA__
ExecStart=__VENV__/bin/gunicorn --workers 1 --threads 4 --timeout 300 --bind unix:__SOCKET__ __PROYECTO__.wsgi:application

[Install]
WantedBy=multi-user.target
