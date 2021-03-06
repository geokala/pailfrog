#!/usr/bin/env python3
"""Pailfrog - Amazon S3 bucket investigation tool."""
from datetime import datetime
import os
import socket
import sys
import xml.etree.ElementTree as ET

from ipaddress import ip_address, ip_network
import requests


# an empty list to contain all the S3 IPV4 ranges.
s3List = []
# an empty list to contain all the IP addresses which are in the S3 IPV4 range.
result_list = []

IPV4_IDENT_STRING = "\"ip_prefix\": \""
IPV6_IDENT_STRING = "\"ipv6_prefix\": \""
IP_TAIL_STRING = "\","
DOMAIN_TEMPLATE = "{domain}.s3.amazonaws.com"


def main(test_domain):
    """Run the bucket investigation tool."""

    # This would be better as an argument, e.g. --update-ranges
    do_amazon_test = "junkdata"
    while do_amazon_test not in ('y', 'Y', 'n', 'N'):
        do_amazon_test = input("Update Amazon IP ranges? Y/N ")
        if do_amazon_test in ('y', 'Y'):
            print("Retrieving updated Amazon IP ranges...")
            update_amazon_ips()
            print("Ranges updated successfully.")
        elif do_amazon_test in ('n', 'N'):
            print("Skipping Amazon range update")

    print("Testing hostname: '" + test_domain + "'.")
    target_domain = DOMAIN_TEMPLATE.format(domain=test_domain)
    target_url = 'http://{}'.format(target_domain)
    print('S3 bucket for {domain} is at {target}'.format(
        domain=test_domain,
        target=target_url,
    ))
    current_ip_address = socket.gethostbyname(target_domain)
    print("IP address of host is: " + current_ip_address)

    with open("./config/sourceIPv4ranges.csv", "r") as source_ips_handle:
        source_ips = source_ips_handle.readlines()

    bucket_in_valid_s3_range = False
    for line in source_ips:
        current_range = ip_network(line.strip().replace(',', ''))
        if ip_address(current_ip_address) in current_range:
            print('Bucket found in s3 range {s3_range}'.format(
                s3_range=str(current_range),
            ))
            bucket_in_valid_s3_range = True
            break

    if bucket_in_valid_s3_range:
        response = requests.get(target_url)

        print('Response for {target} is: {status}'.format(
            target=target_url,
            status=response.status_code,
        ))

        if response.status_code == 200:
            print("S3 root directory is publicly listable. Enumerating files.")
            results = harvest_root(target_url, response.content)
            allowed = results.get(200)
            if allowed:
                print('Allowed:')
                for item in results.get(200):
                    print('  {}'.format(item))
            denied = results.get(403)
            if denied:
                print('Denied:')
                for item in results.get(200):
                    print('  {}'.format(item))
            missing = results.get(404)
            if missing:
                print('Missing:')
                for item in results.get(404):
                    print('  {}'.format(item))
            # TODO: Add json output flag
    else:
        sys.stderr.write(
            'Bucket IP {ip} was not found in any known s3 ranges.\n'.format(
                ip=str(current_ip_address),
            )
        )
        sys.exit(1)


def range_date_check():
    """Check how long since the amazon IP ranges were last updated."""
    if os.path.isfile("./config/source_ips.json"):
        file_modified_on = os.path.getmtime("./config/source_ips.json")
        print("File updated on: " + str(file_modified_on))
        current_time = datetime.now()
        print("Current time: " + str(current_time))
        temp_time = current_time - file_modified_on
        print("Diff is: " + str(temp_time))
        if (current_time - file_modified_on) > 86400:
            update_amazon_ips()
        else:
            print("Amazon IP addresses up to date. Skipping update.")


def update_amazon_ips():
    """Check the current S3 IP addresses are known."""
    response = requests.get("https://ip-ranges.amazonaws.com/ip-ranges.json")
    with open("./config/source_ips.json", "wb") as output_handle:
        output_handle.write(response.content)
    # TODO: If we really want to be dealing with CSV we should just pass it
    # on from here and then return the parsed data, to be written by whatever
    # called it. Avoiding mixing I/O into random functions that aren't named
    # in an obvious way to indicate they will write it will make this harder
    # to maintain later
    parse_amazon_ips()


def parse_amazon_ips():
    """Parse the updated amazon IPs into CSV (why? why not use json?)"""
    # TODO: Note that this should be CIDR notation IPv4/6 ranges
    # TODO: Only use s3 ranges
    source_ips = open("./config/sourceIPs.json", "r")

    ipv4_lines = set()
    ipv6_lines = set()

    for line in source_ips:
        line = line.replace(IP_TAIL_STRING, ",").strip()
        if IPV4_IDENT_STRING in line:
            ipv4_lines.add(line.replace(IPV4_IDENT_STRING, ""))
        elif IPV6_IDENT_STRING in line:
            ipv6_lines.add(line.replace(IPV6_IDENT_STRING, ""))

    if ipv4_lines:
        with open("./config/sourceIPv4ranges.csv", "w") as ipv4_handle:
            for line in ipv4_lines:
                ipv4_handle.write('\n'.join(ipv4_lines))

    if ipv6_lines:
        with open("./config/sourceIPv6ranges.csv", "w") as ipv6_handle:
            for line in ipv6_lines:
                ipv6_handle.write('\n'.join(ipv4_lines))


def find_xml_tags(tree, tag):
    results = []
    for node in tree:
        if node.tag.split('}')[-1] == tag:
            results.append(node)
    return results


def harvest_root(target_url, s3_bucket_in):
    """Enumerate all files found in the bucket for the domain.
    :param bucket: Bucket ID to check.
    :param domain: Domain under which the bucket resides.
    """
    s3_tree = ET.fromstring(s3_bucket_in)
    # ET's handling of namespaces prior to py3.8 is unhelpful
    file_list = find_xml_tags(s3_tree, 'Contents')
    results = {}
    print(str(len(file_list)) + " files found")
    for keys in file_list:
        file_name = find_xml_tags(keys, 'Key')[0].text
        print("Attempting to download " + file_name)
        file_string = target_url + "/" + file_name
        response = requests.get(file_string)
        if response.status_code not in results:
            results[response.status_code] = []
        results[response.status_code].append(file_string)

        if response.status_code == 200:
            dump_accessible_file(response.content, file_name)
    return results


def dump_accessible_file(file_contents, destination):
    print('Writing to {}'.format(destination))
    with open(destination, 'wb') as dump_handle:
        dump_handle.write(file_contents)


if __name__ == "__main__":
    # We could do better here, but for a first pass let's just work with one
    # domain and introduce proper argument parsing when we need it.
    if len(sys.argv) != 2:
        sys.stderr.write('Usage: {} <domain to test>\n'.format(sys.argv[0]))
        sys.exit(1)
    main(sys.argv[1])
