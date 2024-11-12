import boto3
import time
from datetime import datetime
import json
import requests
import pyaudio
import wave
import openai
import atexit
from dotenv import load_dotenv
import os

# .envファイルを読み込む
load_dotenv()

# 環境変数の取得
OPEN_AI_API_KEY = os.getenv('OPEN_AI_API_KEY')
AWS_REGION = os.getenv('AWS_REGION')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
USER_VOICE_OUTPUT_FILENAME = os.getenv('USER_VOICE_OUTPUT_FILENAME')
VOICEROID_OUTPUT_FILENAME = os.getenv('VOICEROID_OUTPUT_FILENAME')
VOICEROID_SYNTHESIS_URI = os.getenv('VOICEROID_SYNTHESIS_URI')
VOICEROID_AUDIO_QUERY_URI = os.getenv('VOICEROID_AUDIO_QUERY_URI')

pyaudioInstance = pyaudio.PyAudio()

def get_file_from_s3(fileName):
    try:
        client = boto3.client('s3', AWS_REGION)
        response = client.get_object(
            Bucket = S3_BUCKET_NAME,
            Key=fileName
        )
        streaming_body = response['Body']
        content = streaming_body.read().decode('utf-8')
    except ClientError:
        print(f"Couldn't get object {fileName} from bucket {S3_BUCKET_NAME}.")
        raise
    else:
        return content

def transcribe_file():
    # S3に保存されているユーザーの音声のwavのURI
    file_uri = f's3://{S3_BUCKET_NAME}/{USER_VOICE_OUTPUT_FILENAME}'

    # transcribeのjob名用の日付
    current_time_str = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    job_name = f'Example-job-{current_time_str}'

    transcribe_client = boto3.client('transcribe', region_name = AWS_REGION)
    transcribe_client.start_transcription_job(
        TranscriptionJobName = job_name,
        Media = {
            'MediaFileUri': file_uri
        },
        MediaFormat = 'wav',
        LanguageCode = 'ja-JP',
        OutputBucketName = S3_BUCKET_NAME
    )

    max_tries = 60
    while max_tries > 0:
        max_tries -= 1
        job = transcribe_client.get_transcription_job(TranscriptionJobName = job_name)
        job_status = job['TranscriptionJob']['TranscriptionJobStatus']
        if job_status in ['COMPLETED', 'FAILED']:
            print(f"Job {job_name} is {job_status}.")
            if job_status == 'COMPLETED':
                file_uri = job['TranscriptionJob']['Transcript']['TranscriptFileUri']
                print(
                    f"Download the transcript from\n"
                    f"\t{file_uri}.")
                break
        else:
            print(f"Waiting for {job_name}. Current status is {job_status}.")
        time.sleep(10)
    return file_uri

def requestVoiceroidQuery(voice_text):
    params = {
        'speaker': 1,
        'text': voice_text
    }

    response = requests.post(VOICEROID_AUDIO_QUERY_URI, params=params)

    if response.status_code == 200:
        print("Success:", response.json())
    else:
        print("Error:", response.status_code)
    return response.json()

def requestAndGetVoiceroidText(voice_text_json):
    headers = {
        'Content-Type': 'application/json'
    }

    params = {
        'speaker': 1
    }

    response = requests.post(VOICEROID_SYNTHESIS_URI, headers=headers, params=params, json=voice_text_json)

    if response.status_code == 200:
        print("Success")
        with open(VOICEROID_OUTPUT_FILENAME, 'wb') as f:
            f.write(response.content)
    else:
        print("Error:", response.status_code)

def getVoiceText(transcribe_file_uri):
    split_result = transcribe_file_uri.split("/")
    content = get_file_from_s3(split_result[-1])
    json_data = json.loads(content)
    voice_text = json_data['results']['audio_segments'][0]['transcript']
    return voice_text

def record_voice(audio):
    try:
        FORMAT = pyaudio.paInt16  # 音声フォーマット
        CHANNELS = 1  # チャンネル数（モノラル）
        RATE = 44100  # サンプリングレート
        CHUNK = 2048  # 1回あたりのフレーム数
        RECORD_SECONDS = 10
        stream = audio.open(format=FORMAT,
                            channels=CHANNELS,
                            rate=RATE,
                            input=True,
                            frames_per_buffer=CHUNK)
        frames = []
        
        print("Recording...")
        try:
            range_num = int(RATE / CHUNK * RECORD_SECONDS)
            while range_num >= 0:
                data = stream.read(CHUNK)
                frames.append(data)
                range_num = range_num - 1
        finally:
            stream.stop_stream()
            stream.close()

        # 録音データを保存
        wf = wave.open(USER_VOICE_OUTPUT_FILENAME, 'wb')
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(audio.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))
        wf.close()
    except KeyboardInterrupt:
        pass

def upload_wav_to_s3():
    bucket_name = S3_BUCKET_NAME
    file_name = USER_VOICE_OUTPUT_FILENAME
    object_name = file_name
    # S3クライアントを作成
    s3_client = boto3.client('s3', region_name = AWS_REGION)

    try:
        # S3にファイルをアップロード
        s3_client.upload_file(file_name, bucket_name, object_name)
        print(f"{file_name} を {bucket_name}/{object_name} にアップロードしました。")
        return True
    except FileNotFoundError:
        print(f"ファイル {file_name} が見つかりません。")
        return False
    except NoCredentialsError:
        print("AWS認証情報が見つかりません。")
        return False
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        return False

def chat_with_gpt(message):
    api_key = OPEN_AI_API_KEY
    messages = [
        {"role": "user", "content": message}
    ]

    openai.api_key = api_key

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=messages
        )
        return response.choices[0].message['content']
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        return None
    
def play_wav(file_path):
    try:
        wf = wave.open(file_path, 'rb')

        stream = pyaudioInstance.open(format=pyaudioInstance.get_format_from_width(wf.getsampwidth()),
                        channels=wf.getnchannels(),
                        rate=wf.getframerate(),
                        output=True)

        chunk = 2048
        data = wf.readframes(chunk)

        while data:
            stream.write(data)
            data = wf.readframes(chunk)

        stream.stop_stream()
        stream.close()

        wf.close()
        print("再生が完了しました。")
    except FileNotFoundError:
        print(f"ファイルが見つかりません: {file_path}")
    except Exception as e:
        print(f"エラーが発生しました: {e}")


def main():
    # 録音する
    record_voice(pyaudioInstance)

    # S3にアップロードする
    upload_wav_to_s3()

    # transcribe呼び出し、結果のjsonURI取得
    transcribe_file_uri = transcribe_file()

    # S3からtranscribeのJsonを取得、JSONからtranscript部分を取得
    text = getVoiceText(transcribe_file_uri)

    response = chat_with_gpt(text)
    if response:
        print("ChatGPTの応答:", response)
    else:
        print("応答を取得できませんでした。")

    # ボイスロイドの読み上げクエリを作成するリクエスト、クエリ結果を取得
    voice_text_json = requestVoiceroidQuery(response)

    # ボイスロイドの読み上げファイルを作成
    requestAndGetVoiceroidText(voice_text_json)

    # ローカルのwavファイルを再生する
    play_wav(VOICEROID_OUTPUT_FILENAME)

def terminatePyaudio():
    pyaudioInstance.terminate

if __name__ == '__main__':
    main()
    atexit.register(terminatePyaudio)
