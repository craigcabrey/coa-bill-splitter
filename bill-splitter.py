#!/usr/bin/env python3

import argparse
import datetime
import logging
import os
import re
import sys
import tempfile
import time


import psycopg
import selenium.webdriver
import selenium.webdriver.chrome.options
import selenium.webdriver.common.by
import selenium.webdriver.support.expected_conditions
import selenium.webdriver.support.ui
import usaddress
import venmo_api


logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] [%(name)s] %(message)s',
    handlers=[logging.StreamHandler()],
)

LOG = logging.getLogger(__name__)


def get_charging_data(conn, start_date, end_date, home_address):
    total_charged = 0

    with conn.cursor() as cur:
        cur.execute(
            'SELECT id from addresses WHERE house_number = %s AND road = %s;',
            (
                home_address['AddressNumber'],
                f'{home_address["StreetName"]} {home_address["StreetNamePostType"]}'
            ),
        )

        data = cur.fetchone()

        if not data:
            raise RuntimeError()

        address_id, *_ = data

        cur.execute(
            'SELECT charge_energy_added FROM charging_processes WHERE address_id = %s AND start_date >= %s AND end_date <= %s',
            (address_id,start_date,end_date)
        )

        data = cur.fetchall()

    for value, *_ in data:
        total_charged += float(value)

    return total_charged


def init_postgres(username, password, host, database):
    uri = f'postgres://{username}:{password}@{host}/{database}'
    return psycopg.connect(uri)


def generate_venmo_requests(client, users, summary, bill_amount, split_ways, dry_run=False):
    now = datetime.datetime.now()

    bill_amount_split = round(float(bill_amount) / split_ways, 2)

    LOG.info(f'Split is ${bill_amount_split} per person')

    for user in users:
        if dry_run:
            LOG.debug(f'Would send Venmo request of ${bill_amount_split} to user {user.id}')
            continue

        LOG.debug(f'Requesting payment for ${bill_amount_split} from user {user.id}')
        client.payment.request_money(amount=bill_amount_split, note=summary, target_user=user)


def login(driver, login_path, username, password):
    driver.get(login_path)

    selenium.webdriver.support.ui.WebDriverWait(driver, 30).until(
        selenium.webdriver.support.expected_conditions.presence_of_element_located(
            (selenium.webdriver.common.by.By.ID, 'LoginForm')
        )
    )

    login_form = driver.find_element(selenium.webdriver.common.by.By.ID, 'LoginForm')
    username_field = driver.find_element(selenium.webdriver.common.by.By.ID, 'username')
    password_field = driver.find_element(selenium.webdriver.common.by.By.ID, 'password')

    username_field.send_keys(username)
    password_field.send_keys(password)

    login_form.submit()


def parse_bill(driver):
    view_bill_button = selenium.webdriver.support.ui.WebDriverWait(driver, 30).until(
        selenium.webdriver.support.expected_conditions.presence_of_element_located(
            (selenium.webdriver.common.by.By.XPATH, '//*[@id="view-bill-button"]/a')
        )
    )
    view_bill_button.click()

    view_bill_button = selenium.webdriver.support.ui.WebDriverWait(driver, 30).until(
        selenium.webdriver.support.expected_conditions.presence_of_element_located(
            (selenium.webdriver.common.by.By.XPATH, '//*[@id="summary-footer"]/div[1]/div/div[2]/div/button'),
        )
    )
    view_bill_button.click()

    LOG.debug('Triggered download of bill')

    total_amount_element = driver.find_element(
        selenium.webdriver.common.by.By.XPATH,
        '//*[@id="total-amount-due"]',
    )

    bill_due_date_element = driver.find_element(
        selenium.webdriver.common.by.By.XPATH,
        '//*[@id="due-date"]',
    )
    bill_due_date_text = bill_due_date_element.text.lstrip('Due').strip()
    bill_due_date = datetime.datetime.strptime(bill_due_date_text, '%B %d, %Y')

    electric_bill_amount_element = driver.find_element(
        selenium.webdriver.common.by.By.XPATH,
        '//opower-widget-bill-details/div/div/div/div/div/div[2]/div/div[2]/div[1]/div/div[1]/div[2]',
    )

    electric_bill_amount = float(electric_bill_amount_element.text.lstrip('$'))
    total_bill_amount = float(total_amount_element.text.lstrip('$'))

    LOG.info(f'Electric bill is ${electric_bill_amount}')
    LOG.info(f'Total bill is ${total_bill_amount}')

    meter_read_date_range_element = driver.find_element(
        selenium.webdriver.common.by.By.XPATH,
        '//opower-widget-bill-details/div/div/div/div/div/div[2]/div/div[2]/div[1]/div/div[3]',
    )

    meter_read_date_range_start_raw, meter_read_date_range_end_raw = meter_read_date_range_element.text.split('-')

    current_year = datetime.datetime.now().year

    meter_read_date_range_start_text = f'{meter_read_date_range_start_raw.strip()} {current_year}'
    meter_read_date_range_end_text = f'{meter_read_date_range_end_raw.strip()} {current_year}'

    LOG.debug(f'Meter read range is {meter_read_date_range_start_text} - {meter_read_date_range_end_text}')

    meter_read_start_date = datetime.datetime.strptime(meter_read_date_range_start_text, '%b %d %Y')
    meter_read_end_date = datetime.datetime.strptime(meter_read_date_range_end_text, '%b %d %Y')

    energy_usage_element = driver.find_element(
        selenium.webdriver.common.by.By.XPATH,
        '//opower-widget-bill-details/div/div/div/div/div/div[2]/div/div[2]/div[1]/div/div[4]/div[5]/div[1]',
    )

    energy_usage_element_text = energy_usage_element.text.replace(',', '')

    if re.search(r'\d+', energy_usage_element_text):
        energy_usage = float(re.search(r'\d+', energy_usage_element_text).group())
    else:
        energy_usage = 'unknown'

    LOG.debug(f'Energy usage is {energy_usage}')

    time.sleep(15)
    driver.quit()

    return (
        total_bill_amount,
        electric_bill_amount,
        energy_usage,
        meter_read_start_date,
        meter_read_end_date,
        bill_due_date,
    )


def init_venmo(access_token, usernames):
    client = venmo_api.Client(access_token=access_token)
    users = [client.user.get_user(username) for username in usernames]

    return client, users


def init_selenium(download_path, remote_host):
    options = selenium.webdriver.chrome.options.Options()
    options.add_argument('--headless')
    experimental_prefs = {
        'download.prompt_for_download': False,
        'download.directory_upgrade': True,
        'plugins.always_open_pdf_externally': True,
    }

    if remote_host:
        options.add_experimental_option('prefs', experimental_prefs)
        driver = selenium.webdriver.Remote(
            f'http://{remote_host}:4444/wd/hub',
            options=options,
        )
    else:
        experimental_prefs['download.default_directory'] = download_path
        options.add_experimental_option('prefs', experimental_prefs)
        driver = selenium.webdriver.Chrome(options=options)

        LOG.info(f'Bill will download to {download_path}')

    driver.implicitly_wait(60)

    return driver


def parse_args():
    parser = argparse.ArgumentParser(
        description='Download, parse, and automatically split the utility bill',
    )

    parser.add_argument('username', help='Utility login username')
    parser.add_argument('password', help='Utility login password')
    parser.add_argument('--debug', default=False, action='store_true')
    parser.add_argument(
        '--download-path',
        default=tempfile.mkdtemp(),
        help=(
            'Location where the bill will be downloaded (has no effect '
            'if using a remote Selenium instance)'
        ),
    )
    parser.add_argument('--dry-run', default=False, action='store_true')
    parser.add_argument('--login-path', default='http://coautilities.com')
    parser.add_argument('--postgres-host')
    parser.add_argument('--postgres-username')
    parser.add_argument('--postgres-password')
    parser.add_argument('--selenium-remote-host')
    parser.add_argument('--teslamate-home-address', type=usaddress.tag, help='Location of home charging')
    parser.add_argument('--teslamate-postgres-database', default='teslamate')
    parser.add_argument('--venmo-usernames', nargs='+', metavar='username')
    parser.add_argument('--venmo-access-token')
    parser.add_argument('--venmo-split-ways', type=int)
    parser.add_argument('--venmo-due-date-threshold', type=int, default=7)

    args = parser.parse_args()

    if args.dry_run:
        args.debug = True

    if args.venmo_split_ways is None:
        args.venmo_split_ways = len(args.venmo_usernames) + 1

    if args.teslamate_home_address:
        args.teslamate_home_address = args.teslamate_home_address[0]

    return args


def main():
    args = parse_args()

    if args.debug:
        LOG.setLevel(logging.DEBUG)

    driver = init_selenium(args.download_path, args.selenium_remote_host)
    login(driver, args.login_path, args.username, args.password)

    (
        total_bill_amount,
        electric_bill_amount,
        energy_usage,
        meter_read_start_date,
        meter_read_end_date,
        bill_due_date,
    ) = parse_bill(driver)

    summary = (
        f'Amount of ${total_bill_amount} ({energy_usage} kWh) for usage between {meter_read_start_date.strftime("%b %d")} '
        f'and {meter_read_end_date.strftime("%b %d")} due on {bill_due_date.strftime("%b %d")}'
    )

    per_kwh = electric_bill_amount / energy_usage

    LOG.debug(f'Average per kWh cost is ${per_kwh}')

    if all([args.postgres_host, args.postgres_username, args.postgres_password]):
        with init_postgres(
            args.postgres_username,
            args.postgres_password,
            args.postgres_host,
            args.teslamate_postgres_database,
        ) as conn:
            total_charged = get_charging_data(
                conn,
                meter_read_start_date,
                meter_read_end_date,
                args.teslamate_home_address,
            )

            LOG.debug(f'Total charging during meter read window is {total_charged} kWh')
            bill_reduction = round(per_kwh * total_charged, 2)

            LOG.info(f'Bill will be reduced by ${bill_reduction} due to car charging')

            total_bill_amount = total_bill_amount - bill_reduction

    if args.venmo_access_token:
        now = datetime.datetime.now()

        if args.dry_run or all([
            now <= bill_due_date,
            (bill_due_date - now).days < args.venmo_due_date_thresholdk
        ]):
            LOG.info('Bill due date is within threshold, proceeding with Venmo requests')

            venmo, users = init_venmo(args.venmo_access_token, args.venmo_usernames)
            generate_venmo_requests(
                venmo,
                users,
                summary,
                total_bill_amount,
                args.venmo_split_ways,
                args.dry_run,
            )

    return True


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
