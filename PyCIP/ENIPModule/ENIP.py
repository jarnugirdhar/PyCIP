import threading
import socket
import time
import struct
from DataTypesModule.DataParsers import CIPDataStructure
import queue
from DataTypesModule.CPF import *
from .ENIPDataStructures import *
from DataTypesModule.signaling import Signaler

class ENIP_Originator():

    def __init__(self, target_ip, target_port=44818):

        self.target = target_ip
        self.port   = target_port
        self.session_handle = None
        self.keep_alive_rate_s = 60

        self.stream_connections = []
        self.datagram_connections = []
        self.class2_3_out_queue = queue.Queue(50)
        self.class0_1_out_queue = queue.Queue(50)

        self.ignoring_sender_context = 1
        self.internal_sender_context = 0
        self.buffer_size_per_sender_context = 5
        self.internal_buffer = queue.Queue(self.buffer_size_per_sender_context)
        self.sender_context = self.ignoring_sender_context + 1
        self.messager = Signaler()

        #self.TCP_rcv_buffer = bytearray()

        self.add_stream_connection(target_port)
        self.manage_connection = True
        self.connection_thread = threading.Thread(target=self._manage_connection)
        self.connection_thread.start()

    @property
    def connected(self):
        return self.manage_connection

    def get_next_sender_context(self):
        if self.sender_context >= 10000:
            self.sender_context = self.ignoring_sender_context
        self.sender_context += 1
        return self.sender_context

    def add_stream_connection(self, target_port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((self.target, target_port))
        s.setblocking(0)
        self.stream_connections.append(s)

    def add_datagram_connection(self, target_port=2222):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        s.connect((self.target, target_port))
        s.setblocking(0)
        self.datagram_connections.append(s)

    def send_encap(self, data, send_id=None, receive_id=None):
        CPF_Array = CPF_Items()

        if not isinstance(receive_id, int):
            receive_id = self.ignoring_sender_context

        if send_id != None:
            cmd_code = ENIPCommandCode.SendUnitData
            command_specific = SendUnitData(Interface_handle=0, Timeout=0)
            CPF_Array.append(CPF_ConnectedAddress(Connection_Identifier=send_id))
            CPF_Array.append(CPF_ConnectedData(Length=len(data)))
            context = receive_id
        else:
            cmd_code = ENIPCommandCode.SendRRData
            command_specific = SendRRData(Interface_handle=0, Timeout=0)
            CPF_Array.append(CPF_NullAddress())
            CPF_Array.append(CPF_UnconnectedData(Length=len(data)))
            context = receive_id
        command_specific_bytes = command_specific.export_data()
        CPF_bytes = CPF_Array.export_data()


        encap_header = ENIPEncapsulationHeader( cmd_code,
                                                len(command_specific_bytes) + len(CPF_bytes) + len(data),
                                                self.session_handle,
                                                0,
                                                context,
                                                0,
                                                )
        encap_header_bytes = encap_header.export_data()

        self._send_encap(encap_header_bytes + command_specific_bytes + CPF_bytes + data)

        if context == self.ignoring_sender_context:
            return None
        return context

    def _send_encap(self, packet):
        self.class2_3_out_queue.put(packet)

    def register_session(self):
        command_specific = RegisterSession(Protocol_version=1, Options_flags=0)
        command_specific_bytes = command_specific.export_data()
        encap_header = ENIPEncapsulationHeader(ENIPCommandCode.RegisterSession,
                                               len(command_specific_bytes),
                                               0,
                                               0,
                                               self.internal_sender_context,
                                               0,
                                               )
        self._send_encap(encap_header.export_data() + command_specific_bytes)

        time_sleep = 5/1000
        timeout = 5.0
        while self.session_handle == None:
            time.sleep(time_sleep)
            timeout -= time_sleep
            if timeout <= 0:
                for s in self.stream_connections:
                    s.close()
                self.manage_connection = False
                return False
        return True

    def unregister_session(self):
        encap_header = ENIPEncapsulationHeader(ENIPCommandCode.UnRegisterSession,
                                               0,
                                               0,
                                               0,
                                               0,
                                               0,
                                               )
        self._send_encap(encap_header.export_data())
        time.sleep(0.2)
        self.manage_connection = False

    def NOP(self):
        header = ENIPEncapsulationHeader(ENIPCommandCode.NOP, 0, self.session_handle,  0, 0, 0)
        self._send_encap(header.export_data())

    # this ideally will use asyncio to manage connections
    def _manage_connection(self):
        self.TCP_rcv_buffers = {}
        delay = 0.001
        time_out = time.time() + self.keep_alive_rate_s * 0.5
        while self.manage_connection:
            self._class0_1_send_rcv()
            self._class2_3_send_rcv()
            self._ENIP_context_packet_mgmt()
            # keep alive the connection from timing out
            if time.time() > time_out:
                time_out = time.time() + self.keep_alive_rate_s * 0.9
                self.NOP()
            time.sleep(delay)

        # close all connections if no longer active
        self.session_handle = None
        for s in (self.stream_connections + self.datagram_connections):
            s.close()
        return None


    def _class2_3_send_rcv(self):

        for s in self.stream_connections:
            buffer = self.TCP_rcv_buffers.get(s, bytearray())
            # receive
            try:
                buffer += s.recv(65535)
            except BlockingIOError:
                pass

            if len(buffer):
                # all data from tcp stream will be encapsulated
                self._import_encapsulated_rcv(buffer, s)

            # send
            while not self.class2_3_out_queue.empty():
                try:
                    packet = self.class2_3_out_queue.get()
                except:
                    pass
                else:
                    s.send(packet)

    def _class0_1_send_rcv(self):

        for s in self.datagram_connections:
                # receive
                try:
                    datagram_packet = s.recv(65535)
                except BlockingIOError:
                    pass

                if len(datagram_packet):
                    # all data from tcp stream will be encapsulated
                    self._import_IO_rcv(datagram_packet, s)

                # send
                while not self.class0_1_out_queue.empty():
                    try:
                        packet = self.class0_1_out_queue.get()
                    except:
                        pass
                    else:
                        s.send(packet)

    def _ENIP_context_packet_mgmt(self):
        while not self.internal_buffer.empty():
            try:
                packet = self.internal_buffer.get()
            except:
                pass
            else:
                if packet.encapsulation_header.Command == ENIPCommandCode.RegisterSession and self.session_handle == None:
                    self.session_handle = packet.encapsulation_header.Session_Handle

                if packet.encapsulation_header.Command == ENIPCommandCode.UnRegisterSession:
                    self.manage_connection = False

    def _import_encapsulated_rcv(self, packet, socket):
        transport = trans_metadata(socket, 'tcp')

        header    = ENIPEncapsulationHeader()
        offset    = header.import_data(packet)
        packet_length = header.Length + header.header_size
        if offset < 0 or packet_length  > len(packet):
            return -1

        parsed_cmd_spc = None
        CPF_Array = None

        if offset < packet_length:
            parsed_cmd_spc = CommandSpecificParser().import_data(packet, header.Command, response=True, offset=offset)
            offset += parsed_cmd_spc.byte_size
        if offset < packet_length:
            CPF_Array = CPF_Items()
            offset += CPF_Array.import_data(packet, offset)

        parsed_packet = TransportPacket( transport,
                                         header,
                                         parsed_cmd_spc,
                                         CPF_Array,
                                         data=packet[offset:packet_length]
                                        )

        if header.Command == ENIPCommandCode.SendUnitData:
            rsp_identifier = CPF_Array[0].Connection_Identifier
        else:
            rsp_identifier = header.Sender_Context

        parsed_packet.response_id = rsp_identifier
        if header.Command in (ENIPCommandCode.SendUnitData, ENIPCommandCode.SendRRData):
            self.messager.send_message(rsp_identifier, parsed_packet)

        elif header.Command in (ENIPCommandCode.RegisterSession, ENIPCommandCode.UnRegisterSession,
                                ENIPCommandCode.NOP, ENIPCommandCode.ListIdentity, ENIPCommandCode.ListServices):
            self.internal_buffer.put(parsed_packet)

        else:
            print('unsupported ENIP command')

        del packet[:header.Length + header.header_size]

    def _import_IO_rcv(self, packet, socket):
        transport = trans_metadata(socket, 'udp')
        packet_length = len(packet)
        if packet_length <= 6:
            return None

        CPF_Array = CPF_Items()
        offset = CPF_Array.import_data(packet)

        parsed_packet = TransportPacket( transport,
                                         None,
                                         None,
                                         CPF_Array,
                                         data=packet[offset:packet_length]
                                        )

        if len(CPF_Array) and  CPF_Array[0].Type_ID == CPF_Codes.SequencedAddress:
            rsp_identifier = CPF_Array[0].Connection_Identifier
        else:
            return None

        parsed_packet.response_id = rsp_identifier
        self.messager.send_message(rsp_identifier, parsed_packet)

    def __del__(self):
        self.unregister_session()

class trans_metadata():

    def __init__(self, socket, proto):
        self.host = socket.getsockname()
        self.peer = socket.getpeername()
        self.protocall = proto

        self.recevied_time = time.time()

class ENIPEncapsulationHeader():

    ENIPHeaderStruct = '<HHIIQI'

    def __init__(self, Command=None, Length=None, Session_Handle=None, Status=None, Sender_Context=None, Options=0) :

        self.Command        = Command
        self.Length         = Length
        self.Session_Handle = Session_Handle
        self.Status         = Status
        self.Sender_Context = Sender_Context
        self.Options        = Options

    def import_data(self, data, offset=0):
        self.header_size = struct.calcsize(self.ENIPHeaderStruct)
        if len(data) - offset >= self.header_size:
            ENIP_header = struct.unpack(self.ENIPHeaderStruct, data[offset:self.header_size])
            self.Command        = ENIP_header[0]
            self.Length         = ENIP_header[1]
            self.Session_Handle = ENIP_header[2]
            self.Status         = ENIP_header[3]
            self.Sender_Context = ENIP_header[4]
            self.Options        = ENIP_header[5]
            return self.header_size
        return -1

    def export_data(self):
        return struct.pack(self.ENIPHeaderStruct,   self.Command,
                                                    self.Length,
                                                    self.Session_Handle,
                                                    self.Status,
                                                    self.Sender_Context,
                                                    self.Options
                            )

