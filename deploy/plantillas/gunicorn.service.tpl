[Unit]
Description=__CLIENTE__ (__SISTEMA__) - gunicorn
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=__RUTA__
ExecStart=__VENV__/bin/gunicorn --workers 3 --timeout 120 --bind 127.0.0.1:__PUERTO__ __PROYECTO__.wsgi:application
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
