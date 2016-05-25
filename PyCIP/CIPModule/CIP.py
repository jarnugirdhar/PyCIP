
import struct

from DataTypesModule.DataTypes import *
from DataTypesModule.DataParsers import *
from collections import OrderedDict as O_Dict
import random

class forward_open():

    def __init__(self, transport, **kwargs):
        self.trans = transport
        self.struct = CIPDataStructure(
                                        # CIP Responce
                                        ('Service', 'UINT'),
                                        ('General_Status', 'USINT'),
                                        ('Additional_Status', 'USINT'),
                                        # forward open rsp
                                        ('OT_connection_ID', 'UDINT'),
                                        ('TO_connection_ID', 'UDINT'),
                                        ('connection_serial', 'UINT'),
                                        ('O_vendor_ID', 'UINT'),
                                        ('O_serial', 'UDINT'),
                                        ('OT_API', 'UDINT'),
                                        ('TO_API', 'UDINT'),
                                        ('Application_Size', 'USINT'),
                                        ('Reserved', 'USINT'),
                                        ('Data', ['Application_Size', 'BYTE']),

                                        )
        self.packet = self.send(**kwargs)
        if self.packet:
            self.struct.import_data(self.packet.data)
            self.__dict__.update(self.struct.get_dict())

    def send(self, **kwargs):

        path  = EPath_item(SegmentType.LogicalSegment, LogicalType.ClassID, LogicalFormat.bit_8, 6)
        path += EPath_item(SegmentType.LogicalSegment, LogicalType.InstanceID, LogicalFormat.bit_8, 1)
        con_mngr = struct.pack('BB', CIPServiceCode.forward_open, int(len(path)/2))
        con_mngr += path


        path  = EPath_item(SegmentType.LogicalSegment, LogicalType.ClassID, LogicalFormat.bit_8, 2)
        path += EPath_item(SegmentType.LogicalSegment, LogicalType.InstanceID, LogicalFormat.bit_8, 1)

        # build default fwd open parameters
        f_struct = O_Dict()
        f_struct['tick'] = 6
        f_struct['time_out'] = 0x28
        f_struct['OT_connection_ID'] = random.randrange(1, 99999)
        f_struct['TO_connection_ID'] = self.trans.get_next_sender_context()
        f_struct['connection_serial'] = random.randrange(0, 2^16)
        f_struct['O_vendor_ID'] = 88
        f_struct['O_serial'] = 12345678
        f_struct['time_out_multiplier'] = 0
        f_struct['reserved_1'] = 0
        f_struct['reserved_2'] = 0
        f_struct['reserved_3'] = 0
        f_struct['OT_RPI'] = 0x03E7FC18
        f_struct['OT_connection_params'] = 0x43FF
        f_struct['TO_RPI'] = 0x03E7FC18
        f_struct['TO_connection_params'] = 0x43FF
        f_struct['trigger'] = 0xa3
        f_struct['path_len'] = int(len(path)/2)

        # update parameters
        for key in f_struct.keys():
            if key in kwargs:
                f_struct[key] = kwargs[key]

        command_specific = struct.pack('<BBIIHHIBBBBIHIHBB', *f_struct.values())

        sender_context = self.trans.send_encap(con_mngr + command_specific + path, None, True)
        return self.trans.receive_encap(sender_context)

class Basic_CIP():

    def __init__(self, transportLayer, connected=True, **kwargs):
        self.trans = transportLayer
        self.sequence_number = 1
        self.connected = connected
        self.OT_connection_id = None
        self.TO_connection_id = None
        if connected:
            self.forward_open_rsp = forward_open(self.trans, **kwargs)
            self.OT_connection_id = self.forward_open_rsp.OT_connection_ID
            self.TO_connection_id = self.forward_open_rsp.TO_connection_ID

    def explicit_message(self, service, *EPath, receive=True):
        packet = bytearray()
        if self.connected:
            packet += struct.pack('H', self.sequence_number)
            self.sequence_number += 1

        packet.append(service)
        packet.append(len(EPath))
        for item in EPath:
            packet += item

        if receive:
            receive_id = self.TO_connection_id if self.TO_connection_id else 0
        else:
            receive_id = None

        # SEND PACKET
        context = self.trans.send_encap(packet, self.OT_connection_id, receive_id)

        if context:
            response = self.trans.receive_encap(context)
            if response == None:
                return None
            structure = []
            if self.connected:
                message_response = MessageRouterResponseStruct()
            else:
                message_response = MessageRouterResponseStruct_UCMM()
            message_response.import_data(response.data)
            response.CIP = message_response
            response.data = response.data[response.CIP.byte_size:]

            return response
        return None

    def get_attr_single(self, class_int, instance_int, attribute_int):

        class_val = EPath_item(SegmentType.LogicalSegment, LogicalType.ClassID, LogicalFormat.bit_8, class_int)
        insta_val = EPath_item(SegmentType.LogicalSegment, LogicalType.InstanceID, LogicalFormat.bit_8, instance_int)
        attri_val = EPath_item(SegmentType.LogicalSegment, LogicalType.AttributeID, LogicalFormat.bit_8, attribute_int)

        packet = self.explicit_message(CIPServiceCode.get_att_single, class_val, insta_val, attri_val)
        return packet

    def get_attr_all(self, class_int, instance_int):

        class_val = EPath_item(SegmentType.LogicalSegment, LogicalType.ClassID, LogicalFormat.bit_8, class_int)
        insta_val = EPath_item(SegmentType.LogicalSegment, LogicalType.InstanceID, LogicalFormat.bit_8, instance_int)

        packet = self.explicit_message(CIPServiceCode.get_att_all, class_val, insta_val)
        return packet

#vol1 ver 3.18 2-4.2
class MessageRouterResponseStruct(CIPDataStructure):
    global_structure = OrderedDict((('Sequence_Count', 'UINT'),
                                     ('Reply_Service', 'USINT'),
                                     ('Reserved', 'USINT'),
                                     ('General_Status', 'USINT'),
                                     ('Size_of_Additional_Status', 'USINT'),
                                     ('Additional_Status', ['Size_of_Additional_Status', 'WORD']))
                                    )
#vol1 ver 3.18 2-4.2
class MessageRouterResponseStruct_UCMM(CIPDataStructure):
    global_structure = OrderedDict((('Reply_Service', 'USINT'),
                                     ('Reserved', 'USINT'),
                                     ('General_Status', 'USINT'),
                                     ('Size_of_Additional_Status', 'USINT'),
                                     ('Additional_Status', ['Size_of_Additional_Status', 'WORD'])
                                     ))

