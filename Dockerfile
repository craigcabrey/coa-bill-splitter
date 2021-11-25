FROM python:3.9

COPY bill-splitter.py /usr/local/bin/bill-splitter.py
COPY bill-splitter.sh /usr/local/bin/bill-splitter
COPY crontab /etc/cron.d/bill-splitter-crontab
COPY requirements.txt /tmp/

RUN pip install -r /tmp/requirements.txt
RUN apt-get update && apt-get install -y cron
RUN chmod 0644 /etc/cron.d/bill-splitter-crontab
RUN crontab /etc/cron.d/bill-splitter-crontab

CMD ["cron", "-f"]
