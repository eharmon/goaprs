﻿#!/usr/bin/env python

"""goaprs.py: Sync data from a Longitude database to APRS."""

import sqlite3
import argparse
import os
import telnetlib
import sys
import time
import urllib
import json

def convertDegrees(coord):
    deg = int(coord)
    min = abs(coord - deg) * 60
    return [deg, min, '']

def main():
    parser = argparse.ArgumentParser(
        description='Sync Longitude database with APRS')

    parser.add_argument(
        '--db',
        '-d',
        metavar='file',
        nargs='?',
        type=str,
        default='loc_db',
        dest='db_file',
        help='sqlite storage DB for storing waypoints'
    )
    parser.add_argument(
        '--callsign',
        '-c',
        metavar='callsign',
        nargs='?',
        type=str,
        required=True,
        dest='callsign',
        help='amateur radio callsign'
    )
    parser.add_argument(
        '--password',
        '-p',
        metavar='password',
        nargs='?',
        type=str,
        required=True,
        dest='password',
        help='APRS password'
    )
    parser.add_argument(
        '--comment',
        '-C',
        metavar='comment',
        nargs='?',
        type=str,
        default='updated by GoAPRS - http://bit.ly/goaprs',
        dest='message',
        help='custom comment for APRS packet'
    )
    parser.add_argument(
        '--icon',
        '-i',
        metavar='code',
        nargs='?',
        type=str,
        default='$',
        dest='icon',
        help='APRS icon, list of codes available here: ' +
            'http://wa8lmf.net/aprs/APRS_symbols.htm ' + 
            '(currently supports only primary symbol table)'
        )
    parser.add_argument(
        '--host',
        '-H',
        metavar='hostname',
        nargs='?',
        type=str,
        default='noam.aprs2.net',
        dest='hostname',
        help='hostname for APRS connectivity'
    )
    parser.add_argument(
        '--api',
        '-a',
        metavar='key',
        nargs='?',
        type=str,
        dest='api_key',
        help='aprs.fi api key (optionally used to reduce rebroadcasted locations)'
    )
    parser.add_argument(
        '-v',
        action='store_true',
        dest='verbose',
        help='be verbose'
    )

    args = parser.parse_args()

    if not os.path.isfile(args.db_file):
        raise Exception("Database does not exist")
    else:
        conn = sqlite3.connect(args.db_file)
        try:
            conn.execute('select * from tracks_location limit 1')
        except sqlite3.OperationalError:
            raise Exception('Unable to read from database, ' +
                'possible corruption?')

    c = conn.cursor()

    if args.verbose: print 'Fetching newest location...\n'

    c.execute('select * from tracks_location order by timestamp desc limit 1')
    loc = c.fetchone()

    if args.verbose: print 'Checking data...\n'

    loc_date = time.gmtime(loc[0]/1000)
    if args.api_key:
        if args.verbose: print 'Checking last update on aprs.fi...\n'
        params = urllib.urlencode({
            'what': 'loc',
            'name': args.callsign + '-10',
            'apikey': args.api_key,
            'format': 'json',
        })
        aprsfi_json = urllib.urlopen('http://api.aprs.fi/api/get?%s' % params)
        aprsfi = json.loads(aprsfi_json.read())
        if aprsfi['result'] == 'ok':
            old_loc = aprsfi['entries'][0]
            # Check if our location has changed by a significant amount, or if
            # we haven't updated our location in the last 6 hours because we
            # haven't moved.
            if abs(float(old_loc['lat']) - loc[1]) < 0.0001 and \
                abs(float(old_loc['lng']) - loc[2]) < 0.0001 and \
                float(old_loc['lasttime']) > (time.time() - 6 * 60 * 60):
                if args.verbose: print 'Location already submitted, quitting...\n'
                sys.exit()
        else:
            if args.verbose: print 'Problem querying aprs.fi...\n'

    # If our Latitude data is more than 6 hours old, just stop beaconing
    if loc_date < time.gmtime(time.time() - 6 * 60 * 60):
        if args.verbose: print 'Location data old, not beaconing...\n'
        sys.exit()

    if args.verbose: print 'Building packet...\n'

    lat = convertDegrees(loc[1])
    lon = convertDegrees(loc[2])

    if lat[0] > 0:
        lat[2] = 'N'
    else:
        lat[2] = 'S'
    if lon[0] < 0:
        lon[2] = 'W'
    else:
        lon[2] = 'E'

    # We are using the following features of the format:
    #
    # args.callsign-10                      the user's SSID, in this case the callsign plus the value 10, which corresponds to an APRS-IS device
    # >APZG01,TCPIP*,qAC,args.callsign-10   we are directing the data to this person, via the APZG01 software on TCPIP, generated by the client, to the user's SSID
    #       Note: APZ is the unassigned software field, G indicates this software, Google Latitude sync, and 01 indicates version 1.
    # :/                                    begin the packet information, we are using a timestamp but don't support APRS messaging
    # strftime('%H%M%S')                    timestamp
    # <next section>                        format the position data
    # args.icon                             indicate the icon to use from the primary tileset
    # args.message                          the message for this packet the user wanted to bundle
    # /A=000000                             altitude in feet (currently unused)

    aprs_packet = '%s-10>APZG01,TCPIP*,qAC,%s-10:/%sh%02d%05.2f%s/%03d%05.2f%s%s%s' % (
        args.callsign,
        args.callsign,
        time.strftime('%H%M%S', loc_date),
        abs(lat[0]),
        lat[1],
        lat[2],
        abs(lon[0]),
        lon[1],
        lon[2],
        args.icon,
        args.message
    )
    aprs_info_packet = '%s-10>APZG01,TCPIP*,qAC,%s-10:>%s - Accurate to %d meters' % (
        args.callsign,
        args.callsign,
        args.callsign,
        loc[3]
    )

    if args.verbose: print 'Connecting to APRS...\n'

    tel = telnetlib.Telnet(args.hostname, 14580)

    tel.read_until('#')
    tel.write('user %s pass %s vers goaprs 0.1\n' % (
        args.callsign,
        args.password
    ))

    tel.read_until('logresp')
    tel.read_until('verified')
    if args.verbose: print 'Logged in...\n'

    tel.write('%s\r\n' % (aprs_packet))
    tel.write('%s\r\n' % (aprs_info_packet))

    if args.verbose: print 'Packets written...\n'

    tel.close()

    if args.verbose: print 'Done...\n'

if __name__ == '__main__': main()
