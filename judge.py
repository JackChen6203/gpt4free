import pymysql
import re
import base64
import requests
import subprocess
import socket
import time

def check_port(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def kill_process_on_port(port):
    # 在Linux系統上，可以使用lsof -ti:port來找出特定端口的進程ID，然後使用kill -9 pid來強制終止它
    try:
        pid = subprocess.check_output(["lsof", "-ti", f":{port}"])
        pid = pid.decode().strip()
        if pid:
            print(f"正在終止佔用端口 {port} 的進程 {pid}")
            subprocess.check_output(["kill", "-9", pid])
            print(f"進程 {pid} 已被終止")
    except subprocess.CalledProcessError as e:
        print("沒有找到佔用指定端口的進程或無法終止進程")

def start_server():
    server_port = 5500
    if check_port(server_port):
        print(f"端口 {server_port} 已被佔用，嘗試終止相關進程")
        kill_process_on_port(server_port)
    print('正在啟動伺服器')
    server_process = subprocess.Popen(['python', './src/FreeGPT4_Server.py'])
    time.sleep(10)  # 假設10秒足夠伺服器啟動
    return server_process

def connect_to_database():
    pwString = "QVZOU18zQ0ZFcG9lRnlFRU4zX2VvUThL"
    pwBytes = base64.b64decode(pwString)
    pw = pwBytes.decode('utf-8')
    print("正在連接資料庫...")
    return pymysql.connect(
        host='mysql-1bddf0d4-davis1233798-2632.d.aivencloud.com', 
        port=20946, user='avnadmin', password=pw, database='defaultdb', 
        charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)

def clean_text(input_text):
    cleaned_text = re.sub(r'[^\u0000-\uFFFF]', '', input_text)
    return cleaned_text

def reset_is_taken_if_needed(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS count FROM prompts WHERE is_taken = 1")
        count_result = cursor.fetchone()
        if count_result['count'] > 20:
            print("is_taken = 1的數量超過20，正在重設...")
            cursor.execute("UPDATE prompts SET is_taken = 0")
            connection.commit()
            print("所有is_taken已重設為0。")

def reset_prompt(connection, prompt_id):
    with connection.cursor() as cursor:
        cursor.execute("UPDATE prompts SET is_taken = 0 WHERE id = %s", (prompt_id,))
        connection.commit()
        print(f"已重設提示ID {prompt_id} 的 is_taken 為0。")

def get_next_prompt(connection):
    with connection.cursor() as cursor:
        for i in range(1, 21):
            field_name = f"result{i}"
            cursor.execute(f"""
                SELECT prompts.id FROM prompts
                INNER JOIN gpt4_judged_final ON prompts.id = gpt4_judged_final.prompts_id
                WHERE prompts.is_taken = 0 AND gpt4_judged_final.{field_name} IS NULL
                ORDER BY prompts.id ASC
                LIMIT 1
            """)
            result = cursor.fetchone()
            if result:
                prompt_id = result['id']
                cursor.execute("UPDATE prompts SET is_taken = 1 WHERE id = %s", (prompt_id,))
                connection.commit()
                print(f"獲得並設置提示ID {prompt_id} 的 is_taken 為1，欄位：{field_name}")
                return {'id': prompt_id, 'field_name': field_name}
        print("沒有可用的提示ID或空缺欄位")
        reset_is_taken_if_needed(connection)
        return None

def update_field(connection, prompt_id, field_name, decision):
    with connection.cursor() as cursor:
        # 檢查欄位是否已經被其他程序更新
        cursor.execute(f"SELECT {field_name} FROM gpt4_judged_final WHERE prompts_id = %s", (prompt_id,))
        if cursor.fetchone()[field_name] is None:
            # 更新數據庫中的欄位
            cursor.execute(f"UPDATE gpt4_judged_final SET {field_name} = %s WHERE prompts_id = %s AND {field_name} IS NULL", (decision, prompt_id))
            connection.commit()
            print(f"gpt4_judged_final表更新成功，ID {prompt_id}，欄位：{field_name}")
        else:
            print(f"欄位 {field_name} 已被更新，跳過此ID {prompt_id}。")

def process_prompts():
    server_process = start_server()
    connection = connect_to_database()
    try:
        while True:
            prompt_info = get_next_prompt(connection)
            if not prompt_info:
                print("檢查後仍無可用提示或空缺欄位，程式將結束。")
                break
            prompt_id = prompt_info['id']
            field_name = prompt_info['field_name']
            cursor = connection.cursor()
            cursor.execute("SELECT p.trained_result, d.value as description FROM prompts p JOIN descriptions d ON p.cve_id = d.cve_id WHERE p.id = %s", (prompt_id,))
            result = cursor.fetchone()
            content = f"請您實際的使用\n1.修補方法: {result['trained_result']} 來修補\n2.漏洞: {result['description']} 確認實作修補策略是否可修補這個漏洞\n3.只需要回答是或否即可。"
            response = requests.get(f"http://127.0.0.1:5500?text={content}")
            if response.ok:
                decision = clean_text(response.text)
                update_field(connection, prompt_id, field_name, decision)
            else:
                print("伺服器回應非200，嘗試重啟伺服器...")
                server_process.terminate()  # 終止當前伺服器進程
                server_process = start_server()  # 重啟伺服器
                reset_prompt(connection, prompt_id)  # 重設該提示的狀態
                continue
    except Exception as e:
        print(f"發生錯誤：{str(e)}")
    finally:
        if connection and connection.open:
            connection.close()

process_prompts()
