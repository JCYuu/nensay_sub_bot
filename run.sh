#!/bin/sh
#python3 -m dummy_webserver & python3 -m toDusbot
/etc/init.d/apache2 start
python3 -m bot
