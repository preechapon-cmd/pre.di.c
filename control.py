# This file is part of pre.di.c
# pre.di.c, a preamp and digital crossover
# Copyright (C) Roberto Ripio

import time
import socket
import sys
import jack
import math as m

import numpy as np

import base
import init
import predic as pd


# main function for command proccessing
def proccess_commands(
        full_command, state=init.state, curves=init.curves, target = init.target):
    """proccesses commands for predic control"""

    # normally write state, but there are exceptions
    state_write = True
    # control variable for switching to relative commands
    add = False
    # erase warnings
    warnings = []
    # backup state to restore values in case of not enough headroom \
    # or error of any kind
    state_old = state.copy()
    # strips command final characters and split command from arguments
    full_command = full_command.rstrip('\r\n').split()

    if len(full_command) > 0:
        command = full_command[0]
    else:
        command = ''
    if len(full_command) > 1:
        arg = full_command[1]
    else:
        arg = None
    if len(full_command) > 2:
        add = (True if full_command[2] == 'add' else False)
    # initializes gain since it is calculated from level
    gain = pd.calc_gain(state['level'])


    ## auxiliary functions

    def disconnect_outputs(jack_client):
        """disconnect sources from predic audio ports"""

        try:
            for port_group in init.config['audio_ports']:
                for port in port_group:
                    sources = jack_client.get_all_connections(port)
                    for source in sources:
                        jack_client.disconnect(source.name, port)
        except Exception:
            print('error disconnecting inputs')


    def bf_cli(command):
        """send commands to brutefir"""

        with socket.socket() as s:
            try:
                s.connect((init.config['bfcli_address'],
                    init.config['bfcli_port']))
                command = f'{command}; quit\n'
                s.send(command.encode())
                if init.config['server_output'] == 2:
                    print('command sent to brutefir')
            except Exception:
                warnings.append('Brutefir error')


    ## internal functions for actions

    def show(throw_it):

        state = pd.show_file()
        return(state)


    def noinput(throw_it, state=state):
        """convenience command that make disconnect_outputs() externally available"""

        try:
            tmp = jack.Client('tmp')
            disconnect_outputs(tmp)
            tmp.close()
        except Exception:
            warnings.append('Something went wrong when disconnecting inputs')
        return state


    def change_target(throw_it):

        try:
            init.target['mag'] = np.loadtxt(init.target_mag_path)
            init.target['pha'] = np.loadtxt(init.target_pha_path)
            state = change_gain(gain)
        except Exception:
            warnings.append('Something went wrong when changing target state')


    def change_input(input, state=state):

        def do_change_input(input_name, source_ports):
            """'source_ports': list [L,R] of jack output ports of chosen source
            """

            # switch
            try:
                tmp = jack.Client('tmp')
                disconnect_outputs(tmp)
                for ports_group in init.config['audio_ports']:
                    # make no more than possible connections,
                    # i.e., minimum of input or output ports
                    num_ports=min(len(ports_group), len(source_ports))
                    for i in range(num_ports):
                        # audio inputs
                        try:
                            tmp.connect(source_ports[i], ports_group[i])
                        except Exception:
                            warnings.append(
                                f'error connecting {source_ports[i]} <--> '
                                f'{ports_group[i]}'
                                )
                tmp.close()
            except Exception:
                # on exception returns False
                warnings.append(f'error changing to input "{input_name}"')
                tmp.close()
                return False
            return True


        state['input'] = input
        try:
            if input is None:
                raise
            elif input in init.inputs:
                if do_change_input (
                        input,
                        init.inputs[state['input']]['source_ports']):
                        # input change went OK
                    state = change_gain(gain)
                    # change xo if configured so
                    if init.config['use_input_xo']:
                        state['xo'] = init.inputs[input]['xo']
                        state = change_xovers(state['xo'])
                else:
                    warnings.append(f'Error changing to input {input}')
                    state['input']  = state_old['input']
                    state['xo'] = state_old['xo']
            else:
                state['input'] = state_old['input']
                warnings.append(
                    f'bad name: input has to be in {list(init.inputs)}'
                    )
                return state
        except Exception:
            state['input']  = state_old['input']
            state['xo'] = state_old['xo']
            warnings.append('Something went wrong when changing input state')
        return state


    def change_xovers(XO_set, state=state):

        state['xo'] = XO_set
        try:
            if XO_set in init.speaker['XO']['sets']:
                coeffs = init.speaker['XO']['sets'][XO_set]
                filters = init.speaker['XO']['filters']
                for i in range(len(filters)):
                    bf_cli(f'cfc "{filters[i]}" "{coeffs[i]}"')
            else:
                state['xo'] = state_old['xo']
                warnings.append(
                    'bad name: XO has to be in '
                    f'{list(init.speaker["XO"]["sets"])}'
                    )
        except Exception:
            state['xo'] = state_old['xo']
            warnings.append('Something went wrong when changing XO state')
        return state


    def change_drc(drc, state=state):

        state['drc'] = drc
        # if drc 'none' coefficient -1 is set, so latency and CPU usage \
        # are improved
        if drc == 'none':
            filters = init.speaker['DRC']['filters']
            for i in range(len(filters)):
                bf_cli(f'cfc "{filters[i]}" -1')
        else:
            try:
                if drc in init.speaker['DRC']['sets']:
                    coeffs = init.speaker['DRC']['sets'][drc]
                    filters = init.speaker['DRC']['filters']
                    for i in range(len(filters)):
                        bf_cli(f'cfc "{filters[i]}" "{coeffs[i]}"')
                else:
                    state['drc'] = state_old['drc']
                    warnings.append(
                        'bad name: DRC has to be in '
                        f'{list(init.speaker["DRC"]["sets"])}'
                        )
            except Exception:
                state['drc'] = state_old['drc']
                warnings.append('Something went wrong when changing DRC state')
        return state


    # following funtions prepares their corresponding actions to be performed \
    # by the change_gain() function


    def change_polarity(polarity, state=state):

        options = ['+', '-', '+-', '-+']
        if polarity in options:
            state['polarity'] = polarity
            try:
                state = change_gain(gain)
            except Exception:
                state['polarity'] = state_old['polarity']
                warnings.append(
                    'Something went wrong when changing polarity state')
        else:
            state['polarity'] = state_old['polarity']
            warnings.append(f'bad option: polarity has to be in {options}')
        return state


    def change_midside(midside, state=state):

        options = ['mid', 'side', 'off']
        if midside in options:
            state['midside'] = midside
            try:
                if state['midside']=='mid':
                    bf_cli( 'cffa "f.eq.L" "f.vol.L" m0.5 ; '
                            'cffa "f.eq.L" "f.vol.R" m0.5 ; '
                            'cffa "f.eq.R" "f.vol.L" m0.5 ; '
                            'cffa "f.eq.R" "f.vol.R" m0.5')
                elif state['midside']=='side':
                    bf_cli( 'cffa "f.eq.L" "f.vol.L" m0.5  ; '
                            'cffa "f.eq.L" "f.vol.R" m-0.5 ; '
                            'cffa "f.eq.R" "f.vol.L" m0.5  ; '
                            'cffa "f.eq.R" "f.vol.R" m-0.5')
                elif state['midside']=='off':
                    bf_cli( 'cffa "f.eq.L" "f.vol.L" m1 ; '
                            'cffa "f.eq.L" "f.vol.R" m0 ; '
                            'cffa "f.eq.R" "f.vol.L" m0 ; '
                            'cffa "f.eq.R" "f.vol.R" m1')
            except Exception:
                state['midside'] = state_old['midside']
                warnings.append('Something went wrong when changing '
                                'midside state')
        else:
            state['midside'] = state_old['midside']
            warnings.append(f'bad option: midside has to be in {options}')
        return state


    def change_mute(mute, state=state):

        options = ['on', 'off']
        if mute in options:
            state['mute'] = mute
            try:
                state = change_gain(gain)
            except Exception:
                state['mute'] = state_old['mute']
                warnings.append(
                    'Something went wrong '
                    'when changing mute state'
                    )
        else:
            state['mute'] = state_old['mute']
            warnings.append(f'bad option: mute has to be in {options}')
        return state


    def change_solo(solo, state=state):

        options = ['off', 'l', 'r']
        if solo in options:
            state['solo'] = solo
            try:
                state = change_gain(gain)
            except Exception:
                state['solo'] = state_old['solo']
                warnings.append('Something went wrong '
                                'when changing solo state')
        else:
            state['solo'] = state_old['solo']
            warnings.append(f'bad option: solo has to be in {options}')
        return state


    def change_loudness(loudness, state=state):

        if loudness in ['on', 'off']:
            state['loudness'] = loudness
            try:
                state = change_gain(gain)
            except Exception:
                state['loudness'] = state_old['loudness']
                warnings.append(
                    'Something went wrong when changing loudness state'
                    )
        else:
            state['mute'] = state_old['mute']
            warnings.append(
                'bad loudness option: has to be "on" or "off"'
                )
        return state


    def change_loudness_ref(loudness_ref, state=state, add=add):

        try:
            state['loudness_ref'] = (float(loudness_ref)
                                     + state['loudness_ref'] * add
                                     )
            # clamp loudness_ref value
            if abs(state['loudness_ref']) > base.loudness_ref_variation:
                state['loudness_ref'] = m.copysign(
                                            base.loudness_ref_variation,
                                            state['loudness_ref']
                                            )
                warnings.append(
                    'loudness reference level must be in the '
                    f'+-{base.loudness_ref_variation} interval'
                    )
                warnings.append('loudness reference level clamped')
            state = change_gain(gain)
        except Exception:
            state['loudness_ref'] = state_old['loudness_ref']
            warnings.append(
                'Something went wrong when changing loudness_ref state'
                )
        return state


    def change_treble(treble, state=state, add=add):

        try:
            state['treble'] = (float(treble)
                                    + state['treble'] * add)
            # clamp treble value
            if m.fabs(state['treble']) > base.tone_variation:
                state['treble'] = m.copysign(
                                    base.tone_variation, state['treble']
                                    )
                warnings.append(
                    'treble must be in the '
                    f'+-{base.tone_variation} interval'
                    )
                warnings.append('treble clamped')
            state = change_gain(gain)
        except Exception:
            state['treble'] = state_old['treble']
            warnings.append('Something went wrong when changing treble state')
        return state


    def change_bass(bass, state=state, add=add):

        try:
            state['bass'] = float(bass) + state['bass'] * add
            # clamp bass value
            if m.fabs(state['bass']) > base.tone_variation:
                state['bass'] = m.copysign(base.tone_variation, state['bass'])
                warnings.append(
                    'bass must be in the '
                    f'+-{base.tone_variation} interval'
                    )
                warnings.append('bass clamped')
            state = change_gain(gain)
        except Exception:
            state['bass'] = state_old['bass']
            warnings.append('Something went wrong when changing bass state')
        return state


    def change_balance(balance, state=state, add=add):

        try:
            state['balance'] = (float(balance)
                                    + state['balance'] * add)
            # clamp balance value
            # 'balance' means deviation from 0 in R channel
            # deviation of the L channel then goes symmetrical
            if m.fabs(state['balance']) > base.balance_variation:
                state['balance'] = m.copysign(
                                        base.balance_variation,
                                        state['balance']
                                        )
                warnings.append(
                    'balance must be in the '
                    f'+-{base.balance_variation} interval'
                    )
                warnings.append('balance clamped')
            state = change_gain(gain)
        except Exception:
            state['balance'] = state_old['balance']
            warnings.append('Something went wrong when changing balance state')
        return state


    def change_level(level, state=state, add=add):

        # level clamp is comissioned to change_gain()
        try:
            state['level'] = (float(level) + state['level'] * add)
            gain = pd.calc_gain(state['level'])
            state = change_gain(gain)
        except Exception:
            state['level'] = state_old['level']
            warnings.append(
                f'Something went wrong when changing {command} state'
                )
        return state


    def change_gain(gain, state=state):
        """change_gain, aka 'the volume machine' :-)"""

        def change_eq():

            eq_str = ''
            l = len(curves['frequencies'])
            for i in range(l):
                eq_str = (f'{eq_str}{curves["frequencies"][i]}/{eq_mag[i]}')
                if i != l:
                    eq_str += ', '
            bf_cli(f'lmc eq "c.eq" mag {eq_str}')
            eq_str = ''
            for i in range(l):
                eq_str = (f'{eq_str}{curves["frequencies"][i]}/{eq_pha[i]}')
                if i != l:
                    eq_str += ', '
            bf_cli(f'lmc eq "c.eq" phase {eq_str}')


        def change_loudness():

            # index of max loudness tones boost
            loudness_max_i = (base.loudness_SPLmax - base.loudness_SPLmin)
            # index of all zeros curve
            loudness_null_i = (base.loudness_SPLmax - base.loudness_SPLref)
            # set curve index
            # higher index means higher boost
            # increasing 'level' decreases boost
            # increasing 'loudness_ref' increases boost
            if state['loudness'] == 'on':
                loudness_i = (
                    loudness_null_i
                    - state['level']
                    + state['loudness_ref']
                    )
            else:
                # all zeros curve
                loudness_i = loudness_null_i
            if loudness_i < 0:
                loudness_i = 0
            if loudness_i > loudness_max_i:
                loudness_i = loudness_max_i
            # loudness_i must be integer as it will be used as \
            # index of loudness curves array
            loudness_i = int(round(loudness_i))
            loudeq_mag = curves['loudness_mag_curves'][:,loudness_i]
            eq_mag = loudeq_mag
            eq_pha = curves['loudness_pha_curves'][:,loudness_i]
            return eq_mag, eq_pha


        def change_treble():

            treble_i = base.tone_variation - state['treble']
            # force integer
            treble_i = int(round(treble_i))
            eq_mag = curves['treble_mag_curves'][:,treble_i]
            eq_pha = curves['treble_pha_curves'][:,treble_i]
            return eq_mag, eq_pha


        def change_bass():

            bass_i = base.tone_variation - state['bass']
            # force integer
            bass_i = int(round(bass_i))
            eq_mag = curves['bass_mag_curves'][:,bass_i]
            eq_pha = curves['bass_pha_curves'][:,bass_i]
            return eq_mag, eq_pha


        def commit_gain(gain):

            bf_atten_dB_l = gain
            bf_atten_dB_r = gain
            # add balance dB gains
            bf_atten_dB_l = bf_atten_dB_l - state['balance']
            bf_atten_dB_r = bf_atten_dB_r + state['balance']
            # from dB to multiplier to implement easily polarity and mute
            # then channel gains are the product of \
            # gain, polarity, mute and solo
            m_mute = {'on': 0, 'off': 1}[state['mute']]
            m_polarity_l = {
                '+': 1, '-': -1, '+-': 1, '-+': -1
                }[state['polarity']]
            m_polarity_r = {
                '+': 1, '-': -1, '+-': -1, '-+': 1
                }[state['polarity']]
            m_solo_l  = {'off': 1, 'l': 1, 'r': 0}[state['solo']]
            m_solo_r  = {'off': 1, 'l': 0, 'r': 1}[state['solo']]
            def m_gain(x): return m.pow(10, x/20) * m_mute
            m_gain_l = (
                m_gain(bf_atten_dB_l)
                * m_polarity_l
                * m_solo_l
                )
            m_gain_r = (
                m_gain(bf_atten_dB_r)
                * m_polarity_r
                * m_solo_r
                )
            # commit final gain change
            bf_cli(f'cfia "f.vol.L" "i.L" m{str(m_gain_l)} ; '
                   f'cfia "f.vol.R" "i.R" m{str(m_gain_r)}')


        # gain command send its str argument directly
        gain = float(gain)
        # clamp gain value
        # just for information, numerical bounds before math range or \
        # math domain error are +6165 dB and -6472 dB
        # max gain is clamped downstream when calculating headroom
        if gain < base.gain_min:
            gain = base.gain_min
            warnings.append(f'min. gain must be more than {base.gain_min} dB')
            warnings.append('gain clamped')
        # EQ curves: loudness + treble + bass
        l_mag,      l_pha      = change_loudness()
        t_mag,      t_pha      = change_treble()
        b_mag,      b_pha      = change_bass()
        # compose EQ curves with target
        eq_mag = init.target['mag'] + l_mag + t_mag + b_mag
        eq_pha = init.target['pha'] + l_pha + t_pha + b_pha
        # calculate headroom
        headroom = pd.calc_headroom(gain, state['balance'], eq_mag)
        # adds input gain. It can lead to clipping \
        # because assumes equal dynamic range between sources
        real_gain = gain + pd.calc_input_gain(state['input'])
        # if enough headroom commit changes
        if headroom >= 0:
            commit_gain(real_gain)
            change_eq()
            state['level'] = pd.calc_level(gain)
        # if not enough headroom tries lowering gain
        else:
            change_gain(gain + headroom)
            print('headroom hit, lowering gain...')
        return state
    # end of change_gain()


    ## parse  commands and select corresponding actions

    try:
        state = {
            'show':             show,
            'noinput':          noinput,
            'target':           change_target,
            'input':            change_input,
            'xo':               change_xovers,
            'drc':              change_drc,
            'polarity':         change_polarity,
            'midside':          change_midside,
            'mute':             change_mute,
            'solo':             change_solo,
            'loudness':         change_loudness,
            'loudness_ref':     change_loudness_ref,
            'treble':           change_treble,
            'bass':             change_bass,
            'balance':          change_balance,
            'level':            change_level,
            'gain':             change_gain
            }[command](arg)
    except KeyError:
        warnings.append(f"Unknown command '{command}'")
    except Exception:
        warnings.append(f"Problems in command '{command}'")

    # return a dictionary of predic state
    return (state, warnings)

# end of proccess_commands()
