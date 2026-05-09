import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from google.auth import default
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from datetime import datetime
import json
from dotenv import load_dotenv

# 環境変数を読み込む
load_dotenv()

app = Flask(__name__)

# LINE Bot 設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
GOOGLE_SHEETS_ID = os.getenv('GOOGLE_SHEETS_ID')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
webhook_handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Google Sheets 認証（環境変数から JSON を読み込む）
def get_sheets_service():
    try:
        # 環境変数から JSON 文字列を取得
        credentials_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        
        if credentials_json:
            # JSON 文字列をパース
            credentials_dict = json.loads(credentials_json)
            credentials = Credentials.from_service_account_info(
                credentials_dict,
                scopes=['https://www.googleapis.com/auth/spreadsheets']
            )
        else:
            # フォールバック（ローカル開発用）
            credentials, project = default()
        
        service = build('sheets', 'v4', credentials=credentials)
        return service
    except Exception as e:
        print(f"Google Sheets 認証エラー: {e}")
        return None

# Google Sheets にデータを追加
def append_to_sheets(values):
    try:
        service = get_sheets_service()
        if not service:
            print("Google Sheets サービス初期化失敗")
            return False
        
        body = {
            'values': [values]
        }
        
        result = service.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEETS_ID,
            range='シート1!A:J',
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        
        print(f"✅ Google Sheets に記録: {result}")
        return True
    
    except HttpError as error:
        print(f"❌ Google Sheets エラー: {error}")
        return False

# Google Sheets からデータを取得（代理店の実績集計用）
def get_agent_stats(agent_name):
    try:
        service = get_sheets_service()
        if not service:
            return None
        
        result = service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEETS_ID,
            range='シート1!A:J'
        ).execute()
        
        values = result.get('values', [])
        
        # 代理店名でフィルタして集計
        referral_count = 0
        total_backup = 0
        
        for row in values[1:]:  # ヘッダーをスキップ
            if len(row) > 0 and row[0] == agent_name:
                referral_count += 1
                # 月間バック額を集計（プラン別に計算）
                if len(row) > 5:
                    plan = row[5]
                    if plan == 'ライト':
                        total_backup += 1980 * 0.5
                    elif plan == 'スタンダード':
                        total_backup += 2980 * 0.5
                    elif plan == 'プロ':
                        total_backup += 3980 * 0.5
        
        return {
            'referral_count': referral_count,
            'total_backup': int(total_backup),
            'profit': int(total_backup) - 3000
        }
    
    except Exception as e:
        print(f"❌ データ取得エラー: {e}")
        return None

# LINE メッセージ処理
@webhook_handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    message_text = event.message.text.strip()
    
    print(f"📨 受信: {user_id} → {message_text}")
    
    # コマンド判定
    if message_text == "今月の実績":
        # 代理店の実績を返す
        stats = get_agent_stats(user_id)
        
        if stats:
            reply_text = f"""📊 今月の実績

紹介人数: {stats['referral_count']}人
月間バック: ¥{stats['total_backup']:,}
代理店費用: -¥3,000
利益: ¥{stats['profit']:,}

詳細はダッシュボードをご確認ください。
"""
        else:
            reply_text = "申し訳ありません。実績の取得に失敗しました。\nサポートまでお問い合わせください。"
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
    
    elif message_text.startswith("紹介:"):
        # 紹介情報を記録
        # 形式: 紹介:佐藤花子,スタンダード,2026-05-01
        try:
            parts = message_text.replace("紹介:", "").split(",")
            if len(parts) != 3:
                raise ValueError("形式が正しくありません")
            
            referral_name = parts[0].strip()
            plan = parts[1].strip()
            referral_date = parts[2].strip()
            
            # バック額を計算
            if plan == 'ライト':
                backup_amount = int(1980 * 0.5)
            elif plan == 'スタンダード':
                backup_amount = int(2980 * 0.5)
            elif plan == 'プロ':
                backup_amount = int(3980 * 0.5)
            else:
                raise ValueError(f"不正なプラン: {plan}")
            
            # Google Sheets に記録
            record = [
                user_id,  # 代理店ID
                datetime.now().strftime('%Y-%m-%d'),  # 報告日
                referral_name,  # 紹介者名
                referral_date,  # 紹介日
                plan,  # プラン
                'テスト',  # ステータス（後で更新）
                backup_amount,  # 月間バック額
                '',  # 累計バック額（自動計算）
                3000,  # 代理店費用
                ''  # 利益（自動計算）
            ]
            
            if append_to_sheets(record):
                reply_text = f"""✅ 紹介情報を記録しました

紹介者: {referral_name}
プラン: {plan}
バック額: ¥{backup_amount:,}

ありがとうございます！
"""
            else:
                reply_text = "申し訳ありません。記録に失敗しました。もう一度お試しください。"
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
        
        except ValueError as e:
            reply_text = f"""❌ エラー: {str(e)}

正しい形式：
紹介:佐藤花子,スタンダード,2026-05-01

プラン: ライト / スタンダード / プロ
"""
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
    
    elif message_text == "サポート":
        reply_text = """📞 サポート

わからないことがあればお気軽にお聞きください。
秀 FP 事務所までご連絡ください。

- 紹介の報告方法
- バック額の確認
- その他のご質問
"""
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
    
    else:
        # デフォルト返信
        reply_text = """こんにちは！Core Compass 代理店システムです。

【使い方】
1️⃣ 紹介を報告
「紹介:佐藤花子,スタンダード,2026-05-01」

2️⃣ 今月の実績を確認
「今月の実績」

3️⃣ サポートを受ける
「サポート」

ご質問があればお気軽にお問い合わせください！
"""
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

# Webhook エンドポイント
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    print(f"リクエスト署名: {signature}")
    print(f"リクエストボディ: {body}")
    
    try:
        webhook_handler.handle(body, signature)
    except InvalidSignatureError:
        print("❌ 署名検証失敗")
        abort(400)
    
    return 'OK'

# ヘルスチェック
@app.route("/health", methods=['GET'])
def health():
    return {'status': 'ok'}, 200

# アプリ起動
if __name__ == '__main__':
    print("🚀 LINE 代理店紹介管理システムが起動しました")
    print("Webhook URL: http://localhost:5000/callback")
    app.run(port=5000, debug=True)
