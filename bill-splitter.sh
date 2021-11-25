#!/bin/sh

/usr/local/bin/bill-splitter.py \
	$USERNAME \
	$PASSWORD \
	--selenium-remote-host \
	$SELENIUM_REMOTE_HOST \
	--postgres-host \
	$POSTGRES_HOST \
	--postgres-username \
	$POSTGRES_USERNAME \
	--postgres-password \
	$POSTGRES_PASSWORD \
	--teslamate-home-address \
	$TESLAMATE_HOME_ADDRESS \
	--venmo-usernames \
	$VENMO_USERNAMES \
	--venmo-access-token \
	$VENMO_ACCESS_TOKEN
