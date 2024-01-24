import paho.mqtt.client as mqtt
from multiprocessing import process
import time
import json
import os
import subprocess
import re
import urllib.request
import calendar
import math

publisher: mqtt.Client = None
broker_address="127.0.0.1"
token="5dfca9229a6195640e3c6052cbb461f838bb6b5d"
mug_ntp_base_uri = ""

def is_db_entry_initialized():
    contents = urllib.request.urlopen("http://127.0.0.1/esmond/perfsonar/archive").read()
    jsonstring = json.loads(contents)
    for item in jsonstring:
        if "tool-name" in item and item["tool-name"] == "mug_ntp_sync":
            global mug_ntp_base_uri

            for curr_type in item["event-types"]:
                if curr_type["event-type"] == "histogram-owdelay":
                    mug_ntp_base_uri = curr_type["base-uri"]
                    return True
            break
    return False

def sync_ntp(targetip, adjust_time = False):
    out = ""
    if(adjust_time):
        subproc = subprocess.Popen("systemctl stop ntpd", stdout=subprocess.PIPE, shell=True )
        subproc.communicate()
        targetntp = "sudo ntpdate " + targetip
        subproc = subprocess.Popen(targetntp, stdout=subprocess.PIPE, shell=True )
        out,err = subproc.communicate()
        subproc = subprocess.Popen("systemctl start ntpd", stdout=subprocess.PIPE, shell=True )
        subproc.communicate()
    else:
        targetntp = "sudo ntpdate -q " + targetip
        subproc = subprocess.Popen(targetntp, stdout=subprocess.PIPE, shell=True )
        out,err = subproc.communicate()
  
    return out

def send_offset_to_db(targetip):
        
        #sync ntp and grab offset
        out = sync_ntp(targetip)
        found_offset = re.search("offset (.*), delay", out.decode("utf-8"))
        offset = found_offset.group(1)     
        #Send offset to db
        cmd = "curl -X POST --dump-header - -H \"Content-Type: application/json\" -H \"Authorization: Token "+token+"\" --data \'{\"ts\": \""+ str(calendar.timegm(time.gmtime()))+"\", \"val\": {\""+offset+"\": 1}}\' http://127.0.0.1" + mug_ntp_base_uri
        ret = subprocess.Popen(cmd,stdout=subprocess.PIPE, shell=True)

def send_client_tests(ip_address, duration):

    json_data = json.dumps({"targetip" : ip_address, "duration" : duration})
    
    #Publish to client and disconnect
    publisher_server=mqtt.Client("publisher_server")
    publisher_server.connect(ip_address,1883,65535 )
    ret = publisher_server.publish("client/client", json_data)
    ret.wait_for_publish()
    publisher_server.disconnect()

def on_msg_client(client, userdata, message):
    json_string = json.loads(message.payload.decode("utf-8"))
    #Client -> Server -> Node-Red

    publisher_server=mqtt.Client("publisher_server")
    publisher_server.connect(broker_address,1883,65535 )
    ret = publisher_server.publish("kpi/server", message.payload.decode("utf-8"))
    ret.wait_for_publish()
    publisher_server.disconnect()


def on_msg_nodered_handover(client, userdata, message):
    json_string = json.loads(message.payload.decode("utf-8"))
    targetip = json_string["targetip"]
    duration = json_string["duration"]


    #Check if custom db entry is initialized
    if(is_db_entry_initialized() == False):
        print("[perfServer:] Db entry hasn't been initialized! Trying to create it manually...")
        #Create custom entry
        cmd = "curl -X POST --dump-header - -H \"Content-Type: application/json\" -H \"Authorization: Token "+token+"\" --data \'{\"subject-type\": \"point-to-point\", \"source\": \"0.0.0.0\", \"destination\": \"0.0.0.0\", \"tool-name\": \"mug_ntp_sync\", \"measurement-agent\": \"0.0.0.0\", \"input-source\": \"0.0.0.0\",\"input-destination\": \"0.0.0.0\",\"time-duration\": \"0\",\"ip-transport-protocol\": \"tcp\",\"event-types\": [{\"event-type\": \"throughput\", \"summaries\": [{\"summary-type\": \"aggregation\", \"summary-window\": 86400}]},{\"event-type\": \"histogram-owdelay\", \"summaries\": [{\"summary-type\": \"aggregation\", \"summary-window\": 86400}]} ]}\' http://127.0.0.1/esmond/perfsonar/archive/"     
        subprocess.Popen(cmd,stdout=open(os.devnull, 'wb'), shell=True)
        time.sleep(2)
        #Check once more
        if(is_db_entry_initialized() == False):
            print("[perfServer:] Fatal error: Wasn't able to create db entry!")
            exit()

    print("[perfServer:] Received handover measurement request")
    publisher.publish("kpi/handover/status", "Received measurement request")

    res = check_target(targetip)
    if(res == 0):
        publisher.publish("kpi/handover/status", "Synchronizing...")
        #Sync before doing anything
        sync_ntp(targetip)
        send_offset_to_db(targetip)
        output = run_owping(targetip, duration)
        publisher.publish("kpi/handover/status", "Processing data")
        print(output)

        json_data1 = []
        json_data2 = []

        blocks = re.findall(r'--- owping statistics from .*? ---(.*?)--- owping statistics from .*? ---', output, re.DOTALL)

        for i, block in enumerate(blocks):
            #print(f"Block {i + 1}:\n{block}\n")
            lines = block.strip().split('\n')
            data_points = []

            for line in lines:
                if line.startswith("seq_no"):
                    parts = re.split(r'\s+', line)
                    seq_no = int(parts[0].split('=')[1])
                    delay_match = re.search(r'delay=(.*?) ms', line)
                    ts_sent_match = re.search(r'sent=(.*?) recv', line)
                    ts_recv_match = re.search(r'recv=(.*?)$', line)
                    lost_match = re.search(r'(\d+) lost', lines[-1])

                    if delay_match and ts_sent_match and ts_recv_match:
                        delay = delay_match.group(1)
                        ts_sent = ts_sent_match.group(1)
                        ts_recv = ts_recv_match.group(1)
                        lost_packet = int(lost_match.group(1)) if lost_match else 0

                        data_points.append({
                            "seq_no": seq_no,
                            "delay": float(delay),
                            "ts_sent": float(ts_sent),
                            "ts_recv": float(ts_recv),
                            "lost_packet": lost_packet
                        })

            if i == 0:
                json_data1 = data_points
            elif i == 1:
                json_data2 = data_points


        #send_client_tests(targetip, duration)
        print("[perfServer:] Sending measurement data to server")
        publisher.publish("kpi/handover/result-lat/down", json.dumps(json_data1, indent=2))
        publisher.publish("kpi/handover/result-lat/up", json.dumps(json_data2, indent=2))
        print("[perfServer:] Finished handover measurement")
    else:
        print("[perfServer:] Target unreachable / Canceled Test")

def run_owping(ip, duration):
    publisher.publish("kpi/handover/status", "Processing...")
    packets = calculate_packets(duration)
    client_message = {
        "duration": duration - 3 }
    print("[perfServer:] Sending measurement request to client")
    publisher.publish("kpi/handover/client", json.dumps(client_message))
    print("[perfServer:] Running owping to client")
    #arguments = [ip, str(packets)]
    #process = subprocess.check_output(['bash', "/home/mqtt/ping.sh"] + arguments, stderr=subprocess.STDOUT)
    command = f"owping -v -U {ip} -c {packets}"
    publisher.publish("Running NTP-sync")
    publisher.publish("kpi/handover/status", "Started measurement")
    process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    return stdout.decode('utf-8')

def check_target(host, max_attempts=2):
    for attempt in range(max_attempts):
        result = subprocess.run(["ping", "-c", "1", host], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if result.returncode != 0:
        # Ping failed all attempts, send MQTT error message
        publisher.publish("kpi/Error", "Host unreachable")

    return result.returncode

def calculate_packets(seconds):
    print("[perfServer:] Calculate amount packetes")
    n=5
    x=[11,12,13,23,38]
    y=[81,91,101,251,351]

    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum([a * b for a, b in zip(x,y)])
    sum_x_squared = sum([a ** 2 for a in x])

    m = (n * sum_xy - sum_x * sum_y) / (n * sum_x_squared - sum_x ** 2)
    b = (sum_y - m * sum_x) / n


    packets = m * seconds + b
    rounded_packets = math.ceil(packets)
    return rounded_packets

def send_mqtt_on_exit():

    ret = publisher.publish("kpi/status", "app terminated")
    ret.wait_for_publish()


def on_msg_nodered_throughput(client, userdata, message):
    print("[perfServer:] Received measurement request")
    ret = publisher.publish("kpi/status","Received measurement request")
    ret.wait_for_publish()
    #Convert message to json object
    json_string = json.loads(message.payload.decode("utf-8"))

    #Extract the arguments
    server_ip = json_string["sourceip"]
    targetip = json_string["targetip"]
    throughput_up = json_string["throughput_up"]
    throughput_down = json_string["throughput_down"]
    latency_up = json_string["latency_up"]
    latency_down = json_string["latency_down"]
    duration = json_string["duration"]
    amount = json_string["amount"]

    #Check if custom db entry is initialized
    if(is_db_entry_initialized() == False):

        print("[perfServer:] Db entry hasn't been initialized! Trying to create it manually...")

        #Create custom entry
        cmd = "curl -X POST --dump-header - -H \"Content-Type: application/json\" -H \"Authorization: Token "+token+"\" --data \'{\"subject-type\": \"point-to-point\", \"source\": \"0.0.0.0\", \"destination\": \"0.0.0.0\", \"tool-name\": \"mug_ntp_sync\", \"measurement-agent\": \"0.0.0.0\", \"input-source\": \"0.0.0.0\",\"input-destination\": \"0.0.0.0\",\"time-duration\": \"0\",\"ip-transport-protocol\": \"tcp\",\"event-types\": [{\"event-type\": \"throughput\", \"summaries\": [{\"summary-type\": \"aggregation\", \"summary-window\": 86400}]},{\"event-type\": \"histogram-owdelay\", \"summaries\": [{\"summary-type\": \"aggregation\", \"summary-window\": 86400}]} ]}\' http://127.0.0.1/esmond/perfsonar/archive/"     
        subprocess.Popen(cmd,stdout=open(os.devnull, 'wb'), shell=True)

        time.sleep(2)
        #Check once more
        if(is_db_entry_initialized() == False):
            print("[perfServer:] Fatal error: Wasn't able to create db entry!")
            exit()

    #Craft our archiver object
    archiver_ip = server_ip
    archive='{"archiver": "esmond","data":{"url":"'"https://"+archiver_ip+"/esmond/perfsonar/archive"'","_auth-token": "'""+token+""'"}}'

    #Check if host is reachable
    print("[perfServer:] Checking target IP")
    res = check_target(targetip)

    if(res == 0):

        #Sync before doing anything
        sync_ntp(targetip, True)
	
        print("[perfServer:] Starting Tests")

        for i in range(amount):
        #Check arguments
            if throughput_up == 1:
                print("[perfServer:] Running throughput_up")
                publisher.publish("kpi/status","Running throughput_up")
                cmd = "pscheduler task --archive '" + archive + "' throughput --dest "+server_ip+" --dest-node "+server_ip+" --source "+targetip+" --source-node "+targetip+" --duration PT" + duration + "S --ip-version 4"
                os.popen(cmd).read()
                ret = publisher.publish("kpi/success_throughput_up", "success_throughput_up")
                ret.wait_for_publish()
            if throughput_down == 1:       
                print("[perfServer:] Running throughput_down")
                publisher.publish("kpi/status","Running throughput_down")      
                cmd = "pscheduler task --archive '" + archive + "' throughput --dest "+targetip+" --dest-node "+targetip+" --source "+server_ip+" --source-node "+server_ip+" --duration PT" + duration + "S --ip-version 4"
                returnval = os.popen(cmd).read()
                ret = publisher.publish("kpi/success_throughput_down", "success_throughput_down")
                ret.wait_for_publish()
            if latency_up == 1:
                print("[perfServer:] Running latency_up")
                publisher.publish("kpi/status","Running latency_up")
                send_offset_to_db(targetip)
                cmd = "pscheduler task --archive '" + archive + "' latency --source " + targetip + " --dest " + server_ip
                returnval = os.popen(cmd).read()
                ret = publisher.publish("kpi/success_latency_up", "success_latency_up")
                ret.wait_for_publish()
            if latency_down == 1:
                print("[perfServer:] Running latency_down")
                publisher.publish("kpi/status","Running latency_down")
                send_offset_to_db(targetip)
                cmd = "pscheduler task --archive '" + archive + "' latency --dest " + targetip + " --source " + server_ip
                returnval = os.popen(cmd).read()
                ret = publisher.publish("kpi/success_latency_down", "success_latency_down")
                ret.wait_for_publish()
        print("[perfServer:] Finished all requested tests.")
        publisher.publish("kpi/status","Finished all requested tests")
        return
    else:
        print("[perfServer:] Target unreachable / Canceled Test")

def main():
    print("[perfServer:] Started")
    global publisher

    #Subscribe to incoming Node-Red Throughput messages
    subscriber_nodered_throughput=mqtt.Client("subscriber")        
    subscriber_nodered_throughput.on_message = on_msg_nodered_throughput
    subscriber_nodered_throughput.connect(broker_address, 1883,65535 )
    subscriber_nodered_throughput.subscribe("kpi/tool")

    #Subscribe to incoming messages from client
    subscriber_client_messages=mqtt.Client("subscriber_server")   
    subscriber_client_messages.on_message = on_msg_client
    subscriber_client_messages.connect(broker_address, 1883,65535 )
    subscriber_client_messages.subscribe("kpi/client")

    #Subscribe to incoming Node-Red Handover messages
    subscriber_nodered_handover=mqtt.Client("subscriber_nodered_handover")   
    subscriber_nodered_handover.on_message = on_msg_nodered_handover
    subscriber_nodered_handover.connect(broker_address, 1883,65535 )
    subscriber_nodered_handover.subscribe("kpi/handover")

    #Connect a publisher for Node-Red
    publisher=mqtt.Client("publisher")
    publisher.connect(broker_address,1883,65535)
    
    subscriber_nodered_throughput.loop_start()
    subscriber_client_messages.loop_start()
    subscriber_nodered_handover.loop_start()

    try:
        input()
    except KeyboardInterrupt:
        send_mqtt_on_exit()

if __name__ == '__main__':
    main()
