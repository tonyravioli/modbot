#!/usr/bin/env python3
# -*- coding: utf-8
import pydle
import json
import logging
import threading
import random
import time
import copy
import operator
import importlib
import subprocess
import datetime

loggingFormat = '%(asctime)s %(levelname)s:%(name)s: %(message)s'
logging.basicConfig(level=logging.DEBUG, format=loggingFormat)

config = json.load(open("config.json"))

BaseClient = pydle.featurize(pydle.features.RFC1459Support, pydle.features.WHOXSupport,
                             pydle.features.AccountSupport, pydle.features.TLSSupport, 
                             pydle.features.IRCv3_1Support)

class Donger(BaseClient):
    def __init__(self, nick, *args, **kwargs):
        super().__init__(nick, *args, **kwargs)
        
        self.mainchannel = config['mainchannel'] # Main channel
        self.opchannel = config['opchannel']
        self.lastheardfrom = {} # lastheardfrom['Polsaker'] = time.time()
        self.sourcehistory = [] # sourcehistory.append(source)
        
        self.poke = False  # True if we poked somebody
        
        timeout_checker = threading.Thread(target = self._timeout)
        timeout_checker.daemon = True
        timeout_checker.start()

        self.import_extcmds()

    def on_connect(self):
        super().on_connect()
        self.join(self.mainchannel)
        self.join(self.opchannel)

    @pydle.coroutine
    def on_message(self, target, source, message):
        if message.startswith("!") or message.startswith(config['nick']):
            command = message[1:].split(" ")[0].lower()
            args = message.rstrip().split(" ")[1:]
            
            if target == self.channel: # Command in main channel
                self.lastheardfrom[source] = time.time()
                self.sourcehistory.append(source)

            # Regular commands
            if command == "thischannel":
                self.message(target, "This will output to the channel the command came from when someone says !thischannel") # because "target" is the channel that it came from
            elif command == "thisuser":
                self.message(source, "This will go to the user in a private message when someone says !thisuser") # because "source" is the user the command came from
            elif command == "help":
                self.message(target, "Still working on that.")
                for ch in self.cmdhelp.keys(): #Extended commands help
                    self.message(source, "  !{}: {}".format(ch, self.cmdhelp[ch]))
            elif command == "version":
                try:
                    ver = subprocess.check_output(["git", "describe", "--tags"]).decode().strip()
                    self.message(target, "I am running {} ({})".format(ver,'working on this'))
                except:
                    self.message(target, "I have no idea.")
            elif command in self.extcmds: #Extended commands support
                try:
                    if self.cmds[command].adminonly and self.users[source]['account'] not in config['admins']:
                        return
                except AttributeError:
                    pass
                self.cmds[command].doit(self, target, source)

    
    def on_quit(self, user, message=None):
        if self.gameRunning:
            self.cowardQuit(user)
    
    def on_part(self, channel, user, message=None):
        if self.gameRunning and channel == self.channel:
            self.cowardQuit(user)
    

    def akick(self, user, time=20, message="FUCKING REKT"):
        # Resolve user account
        user = self.users[user]['account']
        self.message("ChanServ", "AKICK {0} ADD {1} !T {2} {3}".format(self.channel, user, time, message))
    
    def _rename_user(self, user, new):
        if user in self.users:
            self.users[new] = copy.copy(self.users[user])
            self.users[new]['nickname'] = new
            del self.users[user]
        else:
            self._create_user(new)
            if new not in self.users:
                return

        for ch in self.channels.values():
            # Rename user in channel list.
            if user in ch['users']:
                ch['users'].discard(user)
                ch['users'].add(new)


    def chunks(self, l, n):
        """Yield successive n-sized chunks from l."""
        for i in range(0, len(l), n):
            yield l[i:i+n]
    
    def _timeout(self):
        while True:
            time.sleep(5)
            
            if not self.gameRunning or (self.turnStart == 0):
                for i in copy.copy(self.pendingFights):
                    if (time.time() - self.pendingFights[i]['ts'] > 300):
                        self.message(self.channel, "\002{0}\002's challenge has expired.".format(self.pendingFights[i]['players'][0]))
                        del self.pendingFights[i]
                continue
            
            if (time.time() - self.turnStart > 50) and len(self.turnlist) >= (self.currentTurn + 1):
                self.message(self.channel, "\002{0}\002 forfeits due to idle.".format(self.turnlist[self.currentTurn]))
                self.players[self.turnlist[self.currentTurn].lower()]['hp'] = -1
                self.countStat(self.turnlist[self.currentTurn], "idleouts")
                self.kick(self.channel, self.turnlist[self.currentTurn], "WAKE UP SHEEPLE")
                
                aliveplayers = 0
                # TODO: Do this in a neater way
                for p in self.players:
                    if self.players[p]['hp'] > 0:
                        aliveplayers += 1
                        survivor = p
                
                if aliveplayers == 1:
                    self.win(survivor, False)
                else:
                    self.getTurn()
            elif (time.time() - self.turnStart > 30) and len(self.turnlist) >= (self.currentTurn + 1) and not self.poke:
                self.poke = True
                self.message(self.channel, "Wake up, \002{0}\002!".format(self.turnlist[self.currentTurn]))
    
    def _send(self, input):
        super()._send(input)
        if not isinstance(input, str):
            input = input.decode(self.encoding)
        self.logger.debug('>> %s', input.replace('\r\n', ''))

    def _create_user(self, nickname):
        super()._create_user(nickname)
        
        if not self.is_same_nick(self.nickname, nickname):
            if not 'WHOX' in self._isupport:
                if not '.' in nickname:
                    self.whois(nickname)

    def import_extcmds(self):
        self.cmdhelp = {}
        try:
            self.extcmds = config['extendedcommands']
        except KeyError:
            self.extcmds = []
            logging.warning("No extended commands found in config.json")
        logging.info("Beginning extended command tests")
        self.cmds = {}
        for command in self.extcmds:
            try: #Let's test these on start...
                cmd = importlib.import_module('extcmd.{}'.format(command))
                logging.info('Loading extended command: {}'.format(command))
                    
                try: # Handling non-existent helptext
                    self.cmdhelp[command] = cmd.helptext
                except AttributeError:
                    logging.warning('No helptext provided for command {}'.format(command))
                    self.cmdhelp[command] = 'A mystery'
                self.cmds[command] = cmd
            except ImportError:
                logging.warning("Failed to import specified extended command: {}".format(command))
                self.extcmds.remove(command)
                logging.warning("Removed command {} from list of available commands. You should fix config.json to remove it from there, too (or just fix the module).".format(command))
        logging.info('Finished loading all the extended commands')


client = Donger(config['nick'], sasl_username=config['nickserv_username'],
                sasl_password=config['nickserv_password'])
client.connect(config['server'], config['port'], tls=config['tls'])
try:
    client.handle_forever()
except KeyboardInterrupt:
    if client.connected:
        try:
            client.quit(importlib.import_module('extcmd.excuse').doit())
        except:
            client.quit('BRB NAPPING')
