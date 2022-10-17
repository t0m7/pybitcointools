import unittest
import asyncio
from operator import itemgetter
from cryptos import *
from cryptos import coins_async
from cryptos.electrumx_client.types import ElectrumXScripthashNotification
from cryptos.utils import alist
from cryptos.types import Tx
from typing import AsyncGenerator, Any, Union


class BaseAsyncCoinTestCase(unittest.IsolatedAsyncioTestCase):
    name = ""
    unspent_address = ""
    unspent_addresses = []
    unspent = {}
    unspents = []
    addresses = []
    segwit_addresses = []
    new_segwit_addresses = []
    txheight = None
    multisig_address = ""
    privkeys = []
    txid = None
    merkle_txhash = None
    merkle_txheight = None
    txinputs = None
    min_latest_height = 99999999999
    fee = 0
    coin = coins_async.Bitcoin
    blockcypher_api_key = None
    blockcypher_coin_symbol = None
    testnet = True
    num_merkle_siblings = 0
    balance = {}
    balances = []
    history = {}
    raw_tx = ''
    histories = []
    event = asyncio.Event()

    @classmethod
    def setUpClass(cls):
        print('Starting %s tests' % cls.name)

    def setUp(self) -> None:
        self._coin = self.coin(testnet=self.testnet)

    async def asyncTearDown(self) -> None:
        await self._coin.close()

    def assertUnorderedListEqual(self, list1, list2, key):
        list1 = sorted(list1, key=itemgetter(key))
        list2 = sorted(list2, key=itemgetter(key))
        self.assertEqual(list1, list2)

    @property
    def tx(self) -> Tx:
        return deserialize(self.raw_tx)

    async def assertBalanceOK(self):
        result = await self._coin.get_balance(self.unspent_addresses[0])
        self.assertEqual(self.balance, result)

    async def assertGeneratorEqual(self, expected: List[Any], agen: AsyncGenerator[Any, None], order_by: str = None):
        result = await alist(agen)
        if order_by:
            expected = sorted(expected, key=lambda d: d[order_by])
            result = sorted(result, key=lambda d: d[order_by])
        self.assertEqual(expected, result)

    async def assertBalancesOK(self):
        agen = self._coin.get_balances(*self.unspent_addresses)
        await self.assertGeneratorEqual(self.balances, agen)

    async def assertBalanceMerkleProvenOK(self):
        result = await self._coin.balance_merkle_proven(self.unspent_addresses[0])
        self.assertEqual(self.balance['confirmed'], result)

    async def assertBalancesMerkleProvenOK(self):
        balances = [{'address': tx['address'], 'balance': tx['confirmed']} for tx in self.balances]
        agen = self._coin.balances_merkle_proven(*self.unspent_addresses)
        await self.assertGeneratorEqual(balances, agen)

    async def assertHistoryOK(self):
        result = await self._coin.history(self.unspent_addresses[0])
        self.assertEqual(self.history, result)

    async def assertHistoriesOK(self):
        agen = self._coin.get_histories(*self.unspent_addresses, merkle_proof=True)
        await self.assertGeneratorEqual(self.histories, agen, 'tx_hash')

    async def assertUnspentOK(self):
        result = await self._coin.unspent(self.unspent_addresses[0])
        self.assertEqual(self.unspent, result)

    async def assertUnspentsOK(self):
        unspent_outputs = self._coin.get_unspents(*self.unspent_addresses, merkle_proof=True)
        await self.assertGeneratorEqual(self.unspents, unspent_outputs, 'tx_hash')

    async def assertMixedSegwitTransactionOK(self):

        # Find which of the three addresses currently has the most coins and choose that as the sender
        segwit_max_value = 0
        segwit_sender = self.segwit_addresses[0]
        segwit_from_addr_i = 0
        segwit_unspents = []

        for i, addr in enumerate(self.segwit_addresses):
            addr_unspents = await self._coin.unspent(addr)
            value = sum(o['value'] for o in addr_unspents)
            if value > segwit_max_value:
                segwit_max_value = value
                segwit_sender = addr
                segwit_from_addr_i = i
                segwit_unspents = addr_unspents

        regular_max_value = 0
        regular_sender = None
        regular_from_addr_i = 0
        regular_unspents = []

        for i, addr in enumerate(self.addresses):
            addr_unspents = await self._coin.unspent(addr)
            value = sum(o['value'] for o in addr_unspents)
            if value > regular_max_value:
                regular_max_value = value
                regular_sender = addr
                regular_from_addr_i = i
                regular_unspents = addr_unspents

        unspents = segwit_unspents + regular_unspents

        # Arbitrarily set send value, change value, receiver and change address
        outputs_value = segwit_max_value + regular_max_value - self.fee
        send_value = int(outputs_value * 0.5)
        change_value = int(outputs_value - send_value)

        if segwit_sender == self.segwit_addresses[0]:
            receiver = self.segwit_addresses[1]
        elif segwit_sender == self.segwit_addresses[1]:
            receiver = self.segwit_addresses[2]
        else:
            receiver = self.segwit_addresses[0]

        if regular_sender == self.addresses[0]:
            change_address = self.addresses[1]
        elif regular_sender == self.addresses[1]:
            change_address = self.addresses[2]
        else:
            change_address = self.addresses[0]

        outs = [{'value': send_value, 'address': receiver},
                {'value': change_value, 'address': change_address}]

        # Create the transaction using all available unspents as inputs
        tx = self._coin.mktx(unspents, outs)

        segwit_privkey = self.privkeys[segwit_from_addr_i]
        regular_privkey = self.privkeys[regular_from_addr_i]

        # Verify that the private key belongs to the sender address for this network
        self.assertEqual(segwit_sender, self._coin.privtop2w(segwit_privkey),
                         msg=f"Private key does not belong to script {segwit_sender} on {self._coin.display_name}")
        self.assertEqual(regular_sender, self._coin.privtoaddr(regular_privkey),
                         msg=f"Private key does not belong to script {regular_sender} on {self._coin.display_name}")

        self.assertTrue(self._coin.is_segwit(segwit_privkey, segwit_sender))
        self.assertFalse(self._coin.is_segwit(regular_privkey, regular_sender))

        # Sign each input with the given private keys
        for i in range(0, len(segwit_unspents)):
            tx = self._coin.sign(tx, i, segwit_privkey)
        for i in range(len(segwit_unspents), len(unspents)):
            tx = self._coin.sign(tx, i, regular_privkey)

        self.assertEqual(len(tx['witness']), len(unspents))
        tx = serialize(tx)

        # Push the transaction to the network
        result = await self._coin.pushtx(tx)
        self.assertTXResultOK(tx, result)

    async def assertSegwitTransactionOK(self):

        # Find which of the three addresses currently has the most coins and choose that as the sender
        max_value = 0
        sender = self.segwit_addresses[0]
        from_addr_i = 0
        unspents = []

        for i, addr in enumerate(self.segwit_addresses):
            addr_unspents = await self._coin.unspent(addr)
            value = sum(o['value'] for o in addr_unspents)
            if value > max_value:
                max_value = value
                sender = addr
                from_addr_i = i
                unspents = addr_unspents

        # Arbitrarily set send value, receiver and change address
        send_value = int(max_value * 0.1)

        if sender == self.segwit_addresses[0]:
            receiver = self.segwit_addresses[1]
            change_address = self.segwit_addresses[2]
        elif sender == self.segwit_addresses[1]:
            receiver = self.segwit_addresses[2]
            change_address = self.segwit_addresses[0]
        else:
            receiver = self.segwit_addresses[0]
            change_address = self.segwit_addresses[1]

        outs = [{'value': send_value, 'address': receiver}]

        # Create the transaction using all available unspents as inputs
        tx = await self._coin.mktx_with_change(unspents, outs, change=change_address)

        privkey = self.privkeys[from_addr_i]

        # Verify that the private key belongs to the sender address for this network
        self.assertEqual(sender, self._coin.privtop2w(privkey),
                         msg=f"Private key does not belong to script {sender} on {self._coin.display_name}")

        # Sign each input with the given private key
        tx = self._coin.signall(tx, privkey)

        self.assertEqual(len(tx['witness']), len(unspents))

        for i in range(0, len(unspents)):
            self.assertNotEqual(tx['ins'][i]['script'], '')

        # Push the transaction to the network
        result = await self._coin.pushtx(tx)
        self.assertTXResultOK(tx, result)

    async def assertNewSegwitTransactionOK(self):

        # Find which of the three addresses currently has the most coins and choose that as the sender
        max_value = 0
        sender = self.new_segwit_addresses[0]
        from_addr_i = 0
        unspents = []

        for i, addr in enumerate(self.new_segwit_addresses):
            addr_unspents = await self._coin.unspent(addr)
            value = sum(o['value'] for o in addr_unspents)
            if value > max_value:
                max_value = value
                sender = addr
                from_addr_i = i
                unspents = addr_unspents

        # Arbitrarily set send value, change value, receiver and change address
        send_value = int(max_value * 0.1)

        if sender == self.new_segwit_addresses[0]:
            receiver = self.new_segwit_addresses[1]
            change_address = self.new_segwit_addresses[2]
        elif sender == self.new_segwit_addresses[1]:
            receiver = self.new_segwit_addresses[2]
            change_address = self.new_segwit_addresses[0]
        else:
            receiver = self.new_segwit_addresses[0]
            change_address = self.new_segwit_addresses[1]

        outs = [{'value': send_value, 'address': receiver}]

        # Create the transaction using all available unspents as inputs
        tx = await self._coin.mktx_with_change(unspents, outs, change=change_address)

        privkey = self.privkeys[from_addr_i]

        # Verify that the private key belongs to the sender address for this network
        self.assertEqual(sender, self._coin.privtosegwit(privkey),
                         msg=f"Private key does not belong to script {sender} on {self._coin.display_name}")

        # Sign each input with the given private key
        self._coin.signall(tx, privkey)

        self.assertEqual(len(tx['witness']), len(unspents))

        for i in range(0, len(unspents)):
            self.assertEqual(tx['ins'][i]['script'], '')

        tx = serialize(tx)

        # Push the transaction to the network
        result = await self._coin.pushtx(tx)
        self.assertTXResultOK(tx, result)

    def assertTXResultOK(self, tx: Union[str, Tx], result):
        if not isinstance(tx, str):
            tx = serialize(tx)
        tx_hash = public_txhash(tx)
        self.assertEqual(result, tx_hash)
        print("TX %s broadcasted successfully" % result)

    async def assertTransactionOK(self):

        # Find which of the three addresses currently has the most coins and choose that as the sender
        max_value = 0
        sender = self.addresses[0]
        from_addr_i = 0
        unspents = []

        for i, addr in enumerate(self.addresses):
            addr_unspents = await self._coin.unspent(addr)
            value = sum(o['value'] for o in addr_unspents)
            if value > max_value:
                max_value = value
                sender = addr
                from_addr_i = i
                unspents = addr_unspents

        # Arbitrarily set send value, receiver and change address
        send_value = int(max_value * 0.1)

        if sender == self.addresses[0]:
            receiver = self.addresses[1]
            change_address = self.addresses[2]
        elif sender == self.addresses[1]:
            receiver = self.addresses[2]
            change_address = self.addresses[0]
        else:
            receiver = self.addresses[0]
            change_address = self.addresses[1]

        outs = [{'value': send_value, 'address': receiver}]

        # Create the transaction using all available unspents as inputs
        tx = await self._coin.mktx_with_change(unspents, outs, change=change_address)

        privkey = self.privkeys[from_addr_i]

        # Verify that the private key belongs to the sender address for this network
        self.assertEqual(sender, self._coin.privtoaddr(privkey),
                         msg=f"Private key does not belong to script {sender} on {self._coin.display_name}")

        # Sign each input with the given private key
        tx = self._coin.signall(tx, privkey)

        # Serialize and push the transaction to the network
        tx_serialized = serialize(tx)
        result = await self._coin.pushtx(tx)
        self.assertTXResultOK(tx_serialized, result)

    def delete_key_by_name(self, obj, key):
        if isinstance(obj, dict):
            for k, v  in obj.items():
                if k == key:
                    del obj[k]
                    self.delete_key_by_name(obj, key)
                    break
                elif isinstance(v, (dict, list)):
                    self.delete_key_by_name(v, key)
        elif isinstance(obj, list):
            for i in obj:
                self.delete_key_by_name(i, key)

    async def assertGetTXOK(self):
        tx = await self._coin.get_tx(self.txid)
        self.assertListEqual(list(tx.keys()), ['ins', 'outs', 'version', 'locktime', 'tx_hash'])
        self.assertEqual(tx, self.tx)

    async def assertGetSegwitTXOK(self):
        tx = await self._coin.get_tx(self.txid)
        self.assertListEqual(list(tx.keys()), ['ins', 'outs', 'version', 'marker', 'flag', 'witness', 'locktime'])
        self.assertEqual(tx, self.tx)

    async def assertGetSegwitTxsOK(self):
        txs = await alist(self._coin.get_txs(self.txid))
        self.assertListEqual(list(txs[0].keys()),
                             ['ins', 'outs', 'version', 'marker', 'flag', 'witness', 'locktime'])

    async def assertMultiSigTransactionOK(self):
        pubs = [privtopub(priv) for priv in self.privkeys]
        script, sender = self._coin.mk_multsig_address(pubs, 2)
        self.assertEqual(sender, self.multisig_address)
        receiver = self.addresses[0]
        value = 1100000
        tx =  await self._coin.preparetx(sender, receiver, value, self.fee)
        for i in range(0, len(tx['ins'])):
            sig1 = self._coin.multisign(tx, i, script, self.privkeys[0])
            sig3 = self._coin.multisign(tx, i, script, self.privkeys[2])
            tx = apply_multisignatures(tx, i, script, sig1, sig3)
        #Push the transaction to the network
        result = await self._coin.pushtx(tx)
        self.assertTXResultOK(tx, result)

    async def assertBlockHeaderOK(self):
        blockinfo = await self._coin.block_header(self.txheight)
        self.assertListEqual(sorted(blockinfo.keys()),
                             ['bits', 'hash', 'merkle_root', 'nonce', 'prevhash', 'timestamp', 'version']
                             )

    async def assertBlockHeadersOK(self):
        blockinfos = await alist(self._coin.block_headers(self.txheight))
        for blockinfo in blockinfos:
            self.assertListEqual(sorted(blockinfo.keys()),
            ['bits', 'hash', 'merkle_root', 'nonce', 'prevhash', 'timestamp', 'version']
        )

    async def assertMerkleProofOK(self):
        tx = self.unspent[0]
        tx_hash = tx['tx_hash']
        proof = await self._coin.merkle_prove(self.unspent[0])
        self.assertDictEqual(dict(proof), {
            'tx_hash': tx_hash,
            'proven': True
        })

    async def assertSendMultiTXOK(self):

        # Find which of the three addresses currently has the most coins and choose that as the sender
        max_value = 0
        sender = self.addresses[0]
        from_addr_i = 0

        for i, addr in enumerate(self.addresses):
            addr_unspents = await self._coin.unspent(addr)
            value = sum(o['value'] for o in addr_unspents)
            if value > max_value:
                max_value = value
                sender = addr
                from_addr_i = i

        privkey = self.privkeys[from_addr_i]

        # Arbitrarily set send value, change value, receiver and change address
        fee = self.fee * 0.1
        outputs_value = max_value - fee
        send_value1 = int(outputs_value * 0.1)
        send_value2 = int(outputs_value * 0.5)

        if sender == self.addresses[0]:
            receiver1 = self.addresses[1]
            receiver2 = self.addresses[2]
        elif sender == self.addresses[1]:
            receiver1 = self.addresses[2]
            receiver2 = self.addresses[0]
        else:
            receiver1 = self.addresses[0]
            receiver2 = self.addresses[1]

        result = await self._coin.sendmultitx(privkey, sender, [{'address': receiver1, 'value': send_value1},
                                              {'address': receiver2, 'value': send_value2}], fee=self.fee)
        self.assertIsInstance(result, str)
        print("TX %s broadcasted successfully" % result)

    async def assertSendOK(self):

        # Find which of the three addresses currently has the most coins and choose that as the sender
        max_value = 0
        sender = self.addresses[0]
        from_addr_i = 0

        for i, addr in enumerate(self.addresses):
            addr_unspents = await self._coin.unspent(addr)
            value = sum(o['value'] for o in addr_unspents)
            if value > max_value:
                max_value = value
                sender = addr
                from_addr_i = i
                break

        privkey = self.privkeys[from_addr_i]

        # Arbitrarily set send value, change value, receiver and change address
        outputs_value = max_value - self.fee
        send_value = int(outputs_value * 0.1)

        if sender == self.addresses[0]:
            receiver = self.addresses[1]
        elif sender == self.addresses[1]:
            receiver = self.addresses[2]
        else:
            receiver = self.addresses[0]

        result = await self._coin.send(privkey, sender, receiver, send_value, fee=self.fee)
        self.assertIsInstance(result, str)
        print("TX %s broadcasted successfully" % result)

    async def assertSubscribeBlockHeadersOK(self):
        queue = asyncio.Queue()
        block_keys = ['block_height', 'version', 'prev_block_hash', 'merkle_root', 'timestamp', 'bits', 'nonce']
        await self._coin.subscribe_to_block_headers(queue.put)
        result = await queue.get()
        data = result[0]['data']
        self.assertListEqual(list(data.keys()), block_keys)
        await self._coin.unsubscribe_from_block_headers()

    async def assertSubscribeAddressOK(self):
        queue = asyncio.Queue()
        address = self.addresses[0]

        async def add_to_queue(notification: ElectrumXScripthashNotification) -> None:
            await queue.put(notification)

        await self._coin.subscribe_to_address(add_to_queue, address)
        result = await queue.get()
        initial_status = result[0]['data']['status']
        self.assertListEqual(list(result[0].keys()), ['data', 'error', 'method', 'params'])
        await self.assertTransactionOK()
        items = await queue.get()
        data = items[0]
        self.assertListEqual(list(data.keys()), ['address', 'status'])
        self.assertNotEqual(initial_status, data['status'])
        await self._coin.unsubscribe_from_address(address)
