import asyncio
import pyaudio
from dashscope_realtime import DashScopeRealtimeTTS
from dashscope_realtime.tts import TTSConfig

class AudioPlayer:
    def __init__(self, sample_rate=22050, channels=1, sample_width=2):
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width = sample_width
        self.p = pyaudio.PyAudio()
        self.stream = None
        self.audio_queue = []
        self.is_playing = False
        
    def start(self):
        """开始音频播放"""
        try:
            self.stream = self.p.open(
                format=self.p.get_format_from_width(self.sample_width),
                channels=self.channels,
                rate=self.sample_rate,
                output=True,
                frames_per_buffer=1024
            )
            print("🔊 音频播放器已启动")
        except Exception as e:
            print(f"❌ 音频播放器启动失败: {e}")
    
    def play_audio(self, audio_data):
        """播放音频数据"""
        if self.stream and audio_data:
            try:
                self.stream.write(audio_data)
                print(f"🎵 播放音频: {len(audio_data)} 字节")
            except Exception as e:
                print(f"❌ 播放音频失败: {e}")
    
    def stop(self):
        """停止音频播放"""
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        self.p.terminate()
        print("🔇 音频播放器已停止")

async def tts_client(text):
    print(f"🎤 开始TTS合成: {text[:50]}...")
    
    # 创建音频播放器
    player = AudioPlayer(sample_rate=22050, channels=1, sample_width=2)
    player.start()
    
    try:
        # 创建TTS配置
        config = TTSConfig(
            model="cosyvoice-v2",           # 指定模型
            voice="longyumi_v2",        # 指定语音
            volume=80,                      # 音量 (0-100)
            speech_rate=1.025,                # 语速 (0.5-2.0)
            pitch_rate=1.0,                 # 音调 (0.5-2.0)
            sample_rate=22050,              # 采样率
            audio_format="pcm"              # 音频格式
        )
        
        print("🔗 正在连接DashScope TTS服务...")
        
        # 添加音频回调函数来接收和播放音频数据
        def on_audio(audio_data):
            print(f"🔊 收到音频数据: {len(audio_data)} 字节")
            player.play_audio(audio_data)
        
        def on_end():
            print("✅ TTS合成完成")
        
        def on_error(error):
            print(f"❌ TTS错误: {error}")
        
        async with DashScopeRealtimeTTS(
            api_key="sk-adbcd3b16fbd4f96888a90ce11224ee7",
            config=config,
            send_audio=on_audio,
            on_end=on_end,
            on_error=on_error
        ) as tts:
            print("📤 发送文本到TTS服务...")
            await tts.say(text)
            print("🏁 发送完成信号...")
            await tts.finish()
            print("⏳ 等待TTS处理完成...")
            await tts.wait_done()
            print("🎉 所有处理完成!")
            
            # 等待一下确保音频播放完成
            await asyncio.sleep(1)
            
    except Exception as e:
        print(f"💥 TTS客户端异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        player.stop()

async def test():
    print("🚀 开始测试TTS功能...")
    await tts_client("我是您的AI语音助手")
    print("✨ 测试完成!")

if __name__ == "__main__":
    print("🎵 DashScope TTS 测试程序")
    print("=" * 50)
    # 运行测试
    asyncio.run(test())