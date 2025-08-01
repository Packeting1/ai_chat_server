/**
 * FunASR + LLM Web前端应用
 * 智能语音助手客户端
 */

// ===========================
// 常量定义
// ===========================
const CONSTANTS = {
    // WebSocket相关
    WS_ENDPOINTS: {
        STREAM: '/ws/stream',
        UPLOAD: '/ws/upload'
    },
    
    // 音频配置默认值
    AUDIO_DEFAULTS: {
        SAMPLE_RATE: 16000,
        CHUNK_SIZE: 4096,
        SEND_INTERVAL: 100
    },
    
    // 文件上传限制
    FILE_LIMITS: {
        MAX_SIZE: 10 * 1024 * 1024, // 10MB
        ALLOWED_TYPES: ['audio/wav', 'audio/mpeg', 'audio/mp4', 'audio/m4a', 'audio/webm', 'audio/ogg'],
        ALLOWED_EXTENSIONS: /\.(wav|mp3|m4a|webm|ogg)$/i
    },
    
    // UI状态文本
    STATUS_TEXT: {
        DISCONNECTED: '未连接',
        CONNECTING: '正在连接WebSocket...',
        ASR_CONNECTING: '正在连接ASR服务器...',
        LISTENING: '🎤 正在监听...',
        PROCESSING: '🔄 处理中...',
        AI_THINKING: '🤖 AI正在思考...',
        AI_RESPONDING: '🤖 AI正在回答...',
        MIC_STARTING: '正在启动麦克风...',
        MIC_FAILED: '麦克风启动失败',
        CONNECTION_FAILED: '❌ ASR服务器连接失败',
        CONNECTION_CLOSED: '连接已断开'
    },
    
    // 消息类型
    MESSAGE_TYPES: {
        USER_CONNECTED: 'user_connected',
        ASR_CONNECTED: 'asr_connected',
        ASR_CONNECTION_FAILED: 'asr_connection_failed',
        RECOGNITION_PARTIAL: 'recognition_partial',
        RECOGNITION_FINAL: 'recognition_final',
        RECOGNITION_SEGMENT: 'recognition_segment',
        AI_START: 'ai_start',
        AI_CHUNK: 'ai_chunk',
        AI_COMPLETE: 'ai_complete',
        TTS_START: 'tts_start',
        TTS_AUDIO: 'tts_audio',
        TTS_COMPLETE: 'tts_complete',
        TTS_ERROR: 'tts_error',
        ERROR: 'error',
        RESET: 'reset'
    },
    
    // 时间配置
    TIMINGS: {
        PROGRESS_UPDATE_DELAY: 500,
        NEW_SEGMENT_HIDE_DELAY: 3000,
        FADE_OUT_DURATION: 500
    }
};

// ===========================
// 应用状态管理
// ===========================
class AppState {
    constructor() {
        this.websocket = null;
        this.mediaRecorder = null;
        this.audioStream = null;
        this.isStreaming = false;
        this.currentAiMessage = null;
        this.conversationCount = 0;
        this.currentUserId = null;
        
        // TTS相关状态
        this.ttsEnabled = true;
        this.audioContext = null;
        this.audioQueue = [];
        this.isPlayingTTS = false;
        
        // 音频配置
        this.audioConfig = {
            sampleRate: CONSTANTS.AUDIO_DEFAULTS.SAMPLE_RATE,
            chunkSize: CONSTANTS.AUDIO_DEFAULTS.CHUNK_SIZE,
            sendInterval: CONSTANTS.AUDIO_DEFAULTS.SEND_INTERVAL
        };
        
        // 音频处理相关
        this.audioContext = null;
        this.audioProcessor = null;
    }
    
    reset() {
        this.conversationCount = 0;
        this.currentUserId = null;
        this.currentAiMessage = null;
    }
    
    updateAudioConfig(config) {
        this.audioConfig = { ...this.audioConfig, ...config };
    }
}

// 全局状态实例
const appState = new AppState();

// 应用配置
let appConfig = {
    max_conversation_history: 5  // 默认值，会从后端获取
};

// ===========================
// 应用初始化模块
// ===========================
const AppInitializer = {
    /**
     * 从后端获取配置
     */
    async fetchConfig() {
        try {
            const config = await $.getJSON('/api/config');
            appConfig = {
                ...appConfig,
                max_conversation_history: config.max_conversation_history || 5
            };
            console.log('已获取后端配置:', appConfig);
            return appConfig;
        } catch (error) {
            console.warn('获取配置失败，使用默认配置:', error);
            return appConfig;
        }
    }
};

// ===========================
// 工具函数模块
// ===========================
const Utils = {
    /**
     * 获取WebSocket URL
     */
    getWebSocketUrl(endpoint) {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${protocol}//${window.location.host}${endpoint}`;
    },
    
    /**
     * 格式化文件大小
     */
    formatFileSize(bytes) {
        return (bytes / 1024 / 1024).toFixed(2) + 'MB';
    },
    
    /**
     * 格式化时间
     */
    formatTime(ms) {
        return ms + 'ms';
    },
    
    /**
     * 验证音频文件
     */
    validateAudioFile(file) {
        if (!file) {
            return { valid: false, error: '📁 请先选择一个音频文件' };
        }
        
        // 检查文件类型
        const isValidType = CONSTANTS.FILE_LIMITS.ALLOWED_TYPES.includes(file.type) || 
                           CONSTANTS.FILE_LIMITS.ALLOWED_EXTENSIONS.test(file.name);
        
        if (!isValidType) {
            return { 
                valid: false, 
                error: '⚠️ 不支持的文件格式，请选择 WAV、MP3、M4A、WebM 或 OGG 格式的音频文件' 
            };
        }
        
        // 检查文件大小
        if (file.size > CONSTANTS.FILE_LIMITS.MAX_SIZE) {
            return { 
                valid: false, 
                error: '⚠️ 文件太大，请选择小于10MB的音频文件' 
            };
        }
        
        return { valid: true };
    },
    
    /**
     * 防抖函数
     */
    debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    },
    
    /**
     * 节流函数
     */
    throttle(func, limit) {
        let inThrottle;
        return function(...args) {
            if (!inThrottle) {
                func.apply(this, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        };
    }
};

// ===========================
// DOM操作模块 - jQuery版本
// ===========================
const DOMUtils = {
    /**
     * 获取jQuery元素（缓存）
     */
    elements: {},
    
    getElement(id) {
        if (!this.elements[id]) {
            this.elements[id] = $('#' + id);
        }
        return this.elements[id];
    },
    
    /**
     * 批量更新元素文本
     */
    updateTexts(updates) {
        Object.entries(updates).forEach(([elementId, text]) => {
            $('#' + elementId).text(text);
        });
    },
    
    /**
     * 优化的滚动到底部
     */
    scrollToBottom: Utils.throttle(() => {
        const $resultsPanel = $('#resultsPanel');
        if ($resultsPanel.length) {
            $resultsPanel.scrollTop($resultsPanel[0].scrollHeight);
        }
    }, 100)
};

// ===========================
// 错误处理模块
// ===========================
const ErrorHandler = {
    /**
     * 显示用户友好的错误信息
     */
    showError(error, context = '') {
        const message = this.getErrorMessage(error);
        console.error(`${context}:`, error);
        
        // 更新UI显示错误，使用jQuery链式调用
        $('#status').text(message).addClass('error').delay(3000).queue(function() {
            $(this).removeClass('error').dequeue();
        });
        
        // 显示错误弹窗（对于关键错误）
        if (this.isCriticalError(error)) {
            alert(message);
        }
    },
    
    /**
     * 获取用户友好的错误信息
     */
    getErrorMessage(error) {
        if (typeof error === 'string') {
            return error;
        }
        
        if (error.name === 'NotAllowedError') {
            return '❌ 麦克风权限被拒绝，请允许使用麦克风';
        }
        
        if (error.name === 'NotFoundError') {
            return '❌ 未找到麦克风设备';
        }
        
        return error.message || '❌ 发生未知错误';
    },
    
    /**
     * 判断是否为关键错误
     */
    isCriticalError(error) {
        const criticalErrors = ['NotAllowedError', 'NotFoundError'];
        return criticalErrors.includes(error.name);
    }
};

// ===========================
// 主要功能函数开始
// ===========================

// 自动滚动到底部（使用优化版本）
function scrollToBottom() {
    DOMUtils.scrollToBottom();
}

// 全局状态变量 (为了兼容性保留)
let websocket = null;
let mediaRecorder = null;
let audioStream = null;
let isStreaming = false;
let currentAiMessage = null;
let conversationCount = 0;
let currentUserId = null;
let audioConfig = appState.audioConfig;

// ===========================
// WebSocket管理模块
// ===========================
// ===========================
// 连接状态管理
// ===========================
class ConnectionManager {
    constructor() {
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectInterval = 2000; // 2秒
        this.isReconnecting = false;
    }
    
    /**
     * 重置重连状态
     */
    resetReconnection() {
        this.reconnectAttempts = 0;
        this.isReconnecting = false;
    }
    
    /**
     * 检查是否应该重连
     */
    shouldReconnect() {
        return this.reconnectAttempts < this.maxReconnectAttempts && isStreaming;
    }
    
    /**
     * 执行重连
     */
    async attemptReconnect(endpoint, onMessage) {
        if (this.isReconnecting || !this.shouldReconnect()) {
            return null;
        }
        
        this.isReconnecting = true;
        this.reconnectAttempts++;
        
        console.log(`尝试重连 (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`);
        
        DOMUtils.updateTexts({
            status: `🔄 重连中... (${this.reconnectAttempts}/${this.maxReconnectAttempts})`
        });
        
        try {
            // 等待一段时间后重连
            await new Promise(resolve => setTimeout(resolve, this.reconnectInterval));
            
            const ws = await WebSocketManager.connect(endpoint, onMessage);
            this.resetReconnection();
            
            DOMUtils.updateTexts({
                status: CONSTANTS.STATUS_TEXT.LISTENING
            });
            
            return ws;
        } catch (error) {
            console.error(`重连失败 (${this.reconnectAttempts}):`, error);
            this.isReconnecting = false;
            
            if (this.shouldReconnect()) {
                // 继续尝试重连
                setTimeout(() => {
                    this.attemptReconnect(endpoint, onMessage);
                }, this.reconnectInterval);
            } else {
                // 重连失败，停止流
                DOMUtils.updateTexts({
                    status: '❌ 连接断开，重连失败'
                });
                stopStreaming();
            }
            
            return null;
        }
    }
}

const connectionManager = new ConnectionManager();

const WebSocketManager = {
    /**
     * 创建WebSocket连接
     */
    async connect(endpoint, onMessage) {
        const wsUrl = Utils.getWebSocketUrl(endpoint);
        const ws = new WebSocket(wsUrl);
        
        return new Promise((resolve, reject) => {
            const connectTimeout = setTimeout(() => {
                ws.close();
                reject(new Error('连接超时'));
            }, 5000); // 5秒超时
            
            ws.onopen = () => {
                clearTimeout(connectTimeout);
                console.log('WebSocket连接已建立');
                connectionManager.resetReconnection();
                resolve(ws);
            };
            
            ws.onerror = (error) => {
                clearTimeout(connectTimeout);
                console.error('WebSocket连接失败:', error);
                reject(error);
            };
            
            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    onMessage(data);
                } catch (error) {
                    console.error('解析WebSocket消息失败:', error);
                }
            };
            
            ws.onclose = (event) => {
                clearTimeout(connectTimeout);
                console.log('WebSocket连接已关闭:', event.code, event.reason);
                
                if (isStreaming && event.code !== 1000) {
                    // 非正常关闭，尝试重连
                    connectionManager.attemptReconnect(endpoint, onMessage)
                        .then(newWs => {
                            if (newWs) {
                                websocket = newWs;
                            }
                        });
                } else if (isStreaming) {
                    DOMUtils.updateTexts({
                        status: CONSTANTS.STATUS_TEXT.CONNECTION_CLOSED
                    });
                    stopStreaming();
                }
            };
        });
    },
    
    /**
     * 安全发送消息
     */
    safeSend(ws, data) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            try {
                ws.send(data);
                return true;
            } catch (error) {
                console.error('发送消息失败:', error);
                return false;
            }
        }
        return false;
    }
};

async function toggleStreamMode() {
    const $btn = $('#streamBtn');
    const $status = $('#status');
    
    if (!isStreaming) {
        // 更新按钮状态为连接中，但不是最终状态
        $btn.text('连接中...').prop('disabled', true);
        $status.text(CONSTANTS.STATUS_TEXT.CONNECTING);
        
        // 设置ASR连接确认的Promise和超时
        let asrConnectionResolve, asrConnectionReject;
        const asrConnectionPromise = new Promise((resolve, reject) => {
            asrConnectionResolve = resolve;
            asrConnectionReject = reject;
        });
        
        // 存储到全局，供消息处理器使用
        window.asrConnectionPromise = {
            resolve: asrConnectionResolve,
            reject: asrConnectionReject
        };
        
        // 设置5秒超时重置按钮状态
        const connectionTimeout = setTimeout(() => {
            if (!isStreaming) {
                resetButtonToDefault();
                $status.text(CONSTANTS.STATUS_TEXT.CONNECTION_FAILED);
                ErrorHandler.showError('连接超时，请检查ASR服务器是否正常运行', '连接超时');
                asrConnectionReject(new Error('连接超时'));
            }
        }, 5000);
        
        try {
            // 1. 连接WebSocket
            websocket = await WebSocketManager.connect(
                CONSTANTS.WS_ENDPOINTS.STREAM, 
                handleWebSocketMessage
            );
            
            // 2. WebSocket连接成功，等待ASR连接确认
            $status.text(CONSTANTS.STATUS_TEXT.ASR_CONNECTING);
            
            // 3. 等待后端发送ASR连接确认
            await asrConnectionPromise;
            
            // 清除超时定时器
            clearTimeout(connectionTimeout);
            
            // 4. ASR连接成功，启动麦克风
            $status.text(CONSTANTS.STATUS_TEXT.MIC_STARTING);
            
            try {
                await startContinuousRecording();
                
                // 只有在成功启动麦克风后才切换为"停止对话"状态
                $btn.text('停止对话').addClass('active').prop('disabled', false);
                $status.text(CONSTANTS.STATUS_TEXT.LISTENING);
                isStreaming = true;
                
            } catch (micError) {
                ErrorHandler.showError(micError, '麦克风启动失败');
                resetButtonToDefault();
                if (websocket) {
                    websocket.close();
                }
            }
            
        } catch (wsError) {
            // 清除超时定时器
            clearTimeout(connectionTimeout);
            // 重置按钮状态
            resetButtonToDefault();
            ErrorHandler.showError(wsError.message || '连接失败，请检查服务是否正常运行', '连接失败');
        } finally {
            // 清理全局Promise
            window.asrConnectionPromise = null;
        }
    } else {
        stopStreaming();
    }
}

/**
 * 重置按钮到默认状态
 */
function resetButtonToDefault() {
    $('#streamBtn')
        .text('开始持续对话')
        .removeClass('active')
        .prop('disabled', false);
    
    $('#status').text(CONSTANTS.STATUS_TEXT.DISCONNECTED);
}

// ===========================
// 音频处理模块
// ===========================
const AudioManager = {
    /**
     * 获取音频流配置
     */
    getAudioConstraints() {
        return {
            audio: {
                sampleRate: appState.audioConfig.sampleRate,
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true
            }
        };
    },
    
    /**
     * 创建和配置AudioContext
     */
    async createAudioContext() {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        const audioContext = new AudioContext({
            sampleRate: appState.audioConfig.sampleRate
        });
        
        // 确保AudioContext处于运行状态
        if (audioContext.state === 'suspended') {
            await audioContext.resume();
        }
        
        appState.audioContext = audioContext;
        return audioContext;
    },
    
    /**
     * 创建音频处理器（优先使用AudioWorklet）
     */
    async createAudioProcessor(audioContext, source) {
        try {
            // 尝试使用AudioWorklet（更现代，性能更好）
            return await this.createAudioWorkletProcessor(audioContext, source);
        } catch (workletError) {
            console.log('AudioWorklet不可用，使用ScriptProcessor:', workletError);
            return this.createScriptProcessor(audioContext, source);
        }
    },
    
    /**
     * 创建AudioWorklet处理器
     */
    async createAudioWorkletProcessor(audioContext, source) {
        await audioContext.audioWorklet.addModule('data:text/javascript,' + encodeURIComponent(this.getAudioWorkletCode()));
        
        const processor = new AudioWorkletNode(audioContext, 'audio-processor');
        
        // 数据缓冲和发送控制
        const bufferManager = new AudioBufferManager();
        
        processor.port.onmessage = (event) => {
            if (websocket && websocket.readyState === WebSocket.OPEN && isStreaming) {
                bufferManager.processAudioData(new Int16Array(event.data), websocket);
            }
        };
        
        source.connect(processor);
        appState.audioProcessor = processor;
        return processor;
    },
    
    /**
     * 创建ScriptProcessor处理器（兼容方式）
     */
    createScriptProcessor(audioContext, source) {
        const processor = audioContext.createScriptProcessor(appState.audioConfig.chunkSize, 1, 1);
        const bufferManager = new AudioBufferManager();
        
        processor.onaudioprocess = (event) => {
            if (websocket && websocket.readyState === WebSocket.OPEN && isStreaming) {
                const inputBuffer = event.inputBuffer;
                const inputData = inputBuffer.getChannelData(0);
                
                // 转换为16位PCM
                const pcmData = new Int16Array(inputData.length);
                for (let i = 0; i < inputData.length; i++) {
                    pcmData[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32768));
                }
                
                bufferManager.processAudioData(pcmData, websocket);
            }
        };
        
        source.connect(processor);
        processor.connect(audioContext.destination);
        appState.audioProcessor = processor;
        return processor;
    },
    
    /**
     * 获取AudioWorklet代码
     */
    getAudioWorkletCode() {
        return `
            class AudioProcessor extends AudioWorkletProcessor {
                process(inputs, outputs) {
                    const input = inputs[0];
                    if (input.length > 0) {
                        const inputData = input[0];
                        const pcmData = new Int16Array(inputData.length);
                        for (let i = 0; i < inputData.length; i++) {
                            pcmData[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32768));
                        }
                        this.port.postMessage(pcmData.buffer);
                    }
                    return true;
                }
            }
            registerProcessor('audio-processor', AudioProcessor);
        `;
    }
};

// ===========================
// 音频缓冲管理器
// ===========================
class AudioBufferManager {
    constructor() {
        this.audioBuffer = new Int16Array(0);
        this.lastSendTime = 0;
    }
    
    /**
     * 处理音频数据
     */
    processAudioData(newData, websocket) {
        try {
            // 缓冲数据
            this.audioBuffer = this.combineArrays(this.audioBuffer, newData);
            
            // 按间隔发送，减少网络频率
            const now = Date.now();
            if (now - this.lastSendTime >= appState.audioConfig.sendInterval && this.audioBuffer.length > 0) {
                const dataToSend = this.prepareDataForSending();
                
                websocket.send(dataToSend.buffer);
                this.audioBuffer = new Int16Array(0);  // 清空缓冲
                this.lastSendTime = now;
            }
        } catch (error) {
            console.error('处理音频数据失败:', error);
        }
    }
    
    /**
     * 合并数组
     */
    combineArrays(arr1, arr2) {
        const combined = new Int16Array(arr1.length + arr2.length);
        combined.set(arr1);
        combined.set(arr2, arr1.length);
        return combined;
    }
    
    /**
     * 准备发送数据
     */
    prepareDataForSending() {
        return this.audioBuffer;
    }
}

async function startContinuousRecording() {
    try {
        // 获取音频流
        const stream = await navigator.mediaDevices.getUserMedia(AudioManager.getAudioConstraints());
        audioStream = stream;
        
        // 创建AudioContext
        const audioContext = await AudioManager.createAudioContext();
        const source = audioContext.createMediaStreamSource(stream);
        
        // 创建音频处理器
        await AudioManager.createAudioProcessor(audioContext, source);
        
        console.log('音频录制已启动，采样率:', audioContext.sampleRate);
        
    } catch (error) {
        console.error('启动录音失败:', error);
        throw error;
    }
}

// ===========================
// 资源清理模块
// ===========================
const ResourceManager = {
    /**
     * 清理WebSocket连接
     */
    cleanupWebSocket() {
        if (websocket) {
            try {
                if (websocket.readyState === WebSocket.OPEN) {
                    websocket.close(1000, '用户停止对话');
                }
            } catch (error) {
                console.error('关闭WebSocket时出错:', error);
            }
            websocket = null;
        }
    },
    
    /**
     * 清理音频流
     */
    cleanupAudioStream() {
        if (audioStream) {
            try {
                audioStream.getTracks().forEach(track => {
                    track.stop();
                    console.log('音频轨道已停止:', track.kind);
                });
            } catch (error) {
                console.error('停止音频轨道时出错:', error);
            }
            audioStream = null;
        }
    },
    
    /**
     * 清理音频处理器
     */
    cleanupAudioProcessor() {
        if (appState.audioProcessor) {
            try {
                if (appState.audioProcessor.disconnect) {
                    appState.audioProcessor.disconnect();
                }
                if (appState.audioProcessor.port) {
                    appState.audioProcessor.port.close();
                }
            } catch (error) {
                console.error('清理AudioProcessor时出错:', error);
            }
            appState.audioProcessor = null;
        }
    },
    
    /**
     * 清理音频上下文
     */
    cleanupAudioContext() {
        if (appState.audioContext) {
            try {
                if (appState.audioContext.state !== 'closed') {
                    appState.audioContext.close();
                }
            } catch (error) {
                console.error('关闭AudioContext时出错:', error);
            }
            appState.audioContext = null;
        }
    },
    
    /**
     * 清理所有资源
     */
    cleanupAll() {
        this.cleanupWebSocket();
        this.cleanupAudioStream();
        this.cleanupAudioProcessor();
        this.cleanupAudioContext();
    }
};

function stopStreaming() {
    console.log('停止流式对话...');
    
    // 更新状态
    isStreaming = false;
    conversationCount = 0;
    currentUserId = null;
    
    // 重置按钮到默认状态
    resetButtonToDefault();
    
    // 使用jQuery链式调用优雅地更新UI
    $('#currentText').text('等待开始对话...').removeClass('partial-text');
    
    // 更新状态显示
    updateMemoryStatus();
    updateUserInfo(null, 0);
    
    // 清理所有资源
    ResourceManager.cleanupAll();
    
    console.log('✅ 流式对话已停止，资源已清理');
}

// ===========================
// WebSocket消息处理模块
// ===========================
const MessageHandler = {
    /**
     * 主消息处理入口
     */
    handleMessage(data) {
        console.log('收到WebSocket消息:', data.type, data);
        
        const handler = this.messageHandlers[data.type];
        if (handler) {
            handler.call(this, data);
        } else {
            console.warn('未知消息类型:', data.type);
        }
    },
    
    /**
     * 消息处理器映射
     */
    messageHandlers: {
        'user_connected': function(data) {
            currentUserId = data.user_id;
            updateUserInfo(data.user_id, data.active_users);
            console.log(`用户已连接，ID: ${data.user_id}, 在线用户数: ${data.active_users}`);
        },
        
        'asr_connected': function(data) {
            console.log('ASR服务器连接成功:', data.message);
            // 解决ASR连接Promise
            if (window.asrConnectionPromise) {
                window.asrConnectionPromise.resolve(data);
            }
        },
        
        'asr_connection_failed': function(data) {
            console.error('ASR服务器连接失败:', data.message, data.error);
            ErrorHandler.showError(data.message, 'ASR连接失败');
            // 拒绝ASR连接Promise
            if (window.asrConnectionPromise) {
                window.asrConnectionPromise.reject(new Error(data.message));
            }
            // 清理当前用户ID（会话已被后端删除）
            currentUserId = null;
            updateUserInfo(null, 0);
        },
        
        'recognition_partial': function(data) {
            this.updateRecognitionStatus(data.text, true);
        },
        
        'recognition_final': function(data) {
            console.log('最终识别结果:', data.text);
            this.updateRecognitionStatus(data.text, false);
        },
        
        'ai_start': function(data) {
            console.log('AI开始回答，用户输入:', data.user_text);
            this.startAIResponse(data.user_text);
        },
        
        'tts_start': function(data) {
            console.log('TTS开始合成:', data.message);
            DOMUtils.updateTexts({
                status: '🔊 正在合成语音...'
            });
        },
        
        'tts_audio': function(data) {
            console.log('收到TTS音频数据:', data.audio_data.length, '字符');
            TTSManager.playAudioData(data.audio_data, data.sample_rate, data.format);
        },
        
        'tts_complete': function(data) {
            console.log('TTS合成完成:', data.message);
            DOMUtils.updateTexts({
                status: '✅ 语音合成完成'
            });
        },
        
        'tts_error': function(data) {
            console.error('TTS合成错误:', data.error);
            ErrorHandler.showError(data.error, 'TTS错误');
        },
        
        'ai_chunk': function(data) {
            this.processAIChunk(data.content);
        },
        
        'ai_complete': function(data) {
            console.log('AI回答完成，完整回答:', data.full_response);
            this.completeAIResponse();
        },
        
        'error': function(data) {
            ErrorHandler.showError(data.message, 'WebSocket错误');
        },
        
        'llm_test_result': function(data) {
            this.handleLLMTestResult(data);
        }
    },
    
    /**
     * 更新识别状态显示
     */
    updateRecognitionStatus(text, isPartial) {
        const $currentText = $('#currentText');
        
        if (isPartial) {
            $currentText.html(`<span class="partial-text" style="color: #666; font-style: italic;">正在识别: ${text}</span>`);
        } else {
            $currentText.html(`👤 ${text}`);
        }
    },
    
    /**
     * 开始AI响应
     */
    startAIResponse(userText) {
        // 更新状态
        DOMUtils.updateTexts({
            status: CONSTANTS.STATUS_TEXT.AI_THINKING,
            currentText: `👤 ${userText}`
        });
        
        // 添加用户消息到历史
        ConversationManager.addUserMessage(userText);
        
        // 创建AI消息容器
        currentAiMessage = ConversationManager.createAIMessage();
        DOMUtils.scrollToBottom();
    },
    
    /**
     * 处理AI响应片段
     */
    processAIChunk(content) {
        if (currentAiMessage) {
            currentAiMessage.aiContent += content;
            ConversationManager.updateAIMessage(currentAiMessage);
            
            $('#status').text(CONSTANTS.STATUS_TEXT.AI_RESPONDING);
        } else {
            console.error('currentAiMessage为null，无法添加chunk');
        }
    },
    
    /**
     * 完成AI响应
     */
    completeAIResponse() {
        $('#status').text(CONSTANTS.STATUS_TEXT.LISTENING);
        $('#currentText').text('等待您的下一句话...');
        
        currentAiMessage = null;
        conversationCount++;
        updateMemoryStatus();
        DOMUtils.scrollToBottom();
    },
    
    /**
     * 处理LLM测试结果
     */
    handleLLMTestResult(data) {
        const result = data.result;
        const $results = $('#results');
        
        // 移除加载状态
        $('#llm-test-loading').remove();
        
        // 恢复正常状态显示
        if (isStreaming) {
            $('#status').text(CONSTANTS.STATUS_TEXT.LISTENING);
            $('#currentText').text('等待您的下一句话...');
        } else {
            $('#status').text(CONSTANTS.STATUS_TEXT.DISCONNECTED);
            $('#currentText').text('等待开始对话...');
        }
        
        // 创建测试结果显示
        let statusIcon = result.success ? '✅' : '❌';
        let statusText = result.success ? '测试成功' : '测试失败';
        
        const testResultHtml = `
            <div style="border: 2px solid ${result.success ? '#28a745' : '#dc3545'}; border-radius: 8px; margin: 15px 0; overflow: hidden;">
                <div style="background: ${result.success ? '#d4edda' : '#f8d7da'}; padding: 10px; border-bottom: 1px solid ${result.success ? '#c3e6cb' : '#f5c6cb'};">
                    <strong>🧪 LLM连接测试</strong>
                    <span style="float: right; font-size: 12px; color: ${result.success ? '#155724' : '#721c24'};">
                        ${new Date().toLocaleTimeString()}
                    </span>
                </div>
                <div style="padding: 15px;">
                    <div style="font-size: 16px; font-weight: bold; color: ${result.success ? '#155724' : '#721c24'}; margin-bottom: 10px;">
                        ${statusIcon} ${statusText}
                    </div>
                    ${result.success ? `
                        <div style="background: #f8f9fa; border-left: 3px solid #28a745; padding: 10px; margin: 10px 0;">
                            <div style="font-weight: bold; margin-bottom: 5px;">🤖 AI回复: </div>
                            <div style="line-height: 1.6;">${(result.response || '无回复内容').replace(/\n/g, '<br>')}</div>
                        </div>
                        <div style="font-size: 12px; color: #666;">
                            <div>⏱️ 响应时间: ${result.response_time || '未知'}ms</div>
                            <div>🔗 API地址: ${result.api_base || '未知'}</div>
                            <div>🤖 模型: ${result.model || '未知'}</div>
                        </div>
                    ` : `
                        <div style="background: #f8d7da; border-left: 3px solid #dc3545; padding: 10px; margin: 10px 0;">
                            <div style="font-weight: bold; margin-bottom: 5px;">❌ 错误信息:</div>
                            <div>${result.error || '未知错误'}</div>
                            ${result.details ? `<div style="margin-top: 5px; font-size: 12px; color: #666;">${result.details}</div>` : ''}
                        </div>
                    `}
                </div>
            </div>
        `;
        
        $results.prepend(testResultHtml);
        DOMUtils.scrollToBottom();
        
        console.log('LLM测试结果已显示:', result);
    }
};

// ===========================
// 对话管理模块
// ===========================
const ConversationManager = {
    /**
     * 添加用户消息
     */
    addUserMessage(text) {
        const $results = $('#results');
        const messageElement = UIManager.createMessageElement(text, 'user');
        $results.prepend(messageElement);
        return messageElement;
    },
    
    /**
     * 创建AI消息容器
     */
    createAIMessage() {
        const $results = $('#results');
        const $aiDiv = $(`
            <div class="message ai-message">
                <strong>🤖 AI: </strong><span class="ai-content"></span>
                <span class="message-timestamp">${new Date().toLocaleTimeString()}</span>
            </div>
        `);
        $results.prepend($aiDiv);
        
        return {
            element: $aiDiv[0],
            $element: $aiDiv,
            aiContent: ''
        };
    },
    
    /**
     * 更新AI消息内容
     */
    updateAIMessage(messageObj) {
        const $contentSpan = messageObj.$element ? messageObj.$element.find('.ai-content') : $(messageObj.element).find('.ai-content');
        if ($contentSpan.length) {
            // 清理AI内容中的多余换行符
            let content = messageObj.aiContent;
            
            // 清理开头的多余换行符
            content = content.replace(/^\n+/, '');
            
            // 清理多个连续换行符，最多保留两个（显示为一个空行）
            content = content.replace(/\n{3,}/g, '\n\n');
            
            // 转换换行符为HTML并设置HTML内容以保持换行格式
            const htmlContent = content.replace(/\n/g, '<br>');
            $contentSpan.html(htmlContent);
            DOMUtils.scrollToBottom();
        }
    },
    
    /**
     * 清空对话历史
     */
    clearHistory() {
        $('#results').empty();
        currentAiMessage = null;
        conversationCount = 0;
        updateMemoryStatus();
    }
};

function handleWebSocketMessage(data) {
    return MessageHandler.handleMessage(data);
}

function updateMemoryStatus() {
    const $memoryStatus = $('#memoryStatus');
    const maxHistory = appConfig.max_conversation_history;
    
    if (conversationCount === 0) {
        $memoryStatus.text('🧠 AI记忆: 空白状态');
    } else if (conversationCount === 1) {
        $memoryStatus.text('🧠 AI记忆: 记住1轮对话');
    } else if (conversationCount < maxHistory) {
        $memoryStatus.text(`🧠 AI记忆: 记住${conversationCount}轮对话`);
    } else {
        $memoryStatus.text(`🧠 AI记忆: 记住最近${maxHistory}轮对话`);
    }
}

function updateUserInfo(userId, activeUsers) {
    const $userInfo = $('#userInfo');
    if (userId) {
        const shortId = userId.substring(0, 8) + '...';
        $userInfo.text(`👤 用户: ${shortId} | 🌐 在线: ${activeUsers}人`);
        
        // 从连接池获取准确的在线人数
        fetchAccurateOnlineUsers();
    } else {
        $userInfo.text('👤 用户: 未连接 | 🌐 在线: 0人');
    }
}

/**
 * 从连接池API获取准确的在线人数
 */
async function fetchAccurateOnlineUsers() {
    try {
        const response = await $.getJSON('/api/pool/stats/');
        if (response.success && response.stats) {
            const activeUsers = response.stats.active_users || 0;
            
            // 更新用户信息显示，保持用户ID不变
            const $userInfo = $('#userInfo');
            const currentText = $userInfo.text();
            const userPart = currentText.split(' | ')[0]; // 保留用户部分
            $userInfo.text(`${userPart} | 🌐 在线: ${activeUsers}人`);
            
            console.log(`从连接池获取准确在线人数: ${activeUsers}`);
        }
    } catch (error) {
        console.warn('获取连接池状态失败:', error);
        // 如果获取失败，不影响现有显示
    }
}

/**
 * 定期更新在线人数
 */
function startOnlineUsersUpdater() {
    // 每30秒更新一次在线人数
    setInterval(() => {
        if (currentUserId) { // 只有在连接状态下才更新
            fetchAccurateOnlineUsers();
        }
    }, 30000);
}

function resetConversation() {
    // 发送重置消息到服务器
    if (WebSocketManager.safeSend(websocket, JSON.stringify({ type: 'reset' }))) {
        console.log('已发送重置请求到服务器');
    }
    
    // 清空本地对话状态
    ConversationManager.clearHistory();
    
    $('#currentText').text(isStreaming ? '等待您的下一句话...' : '等待开始对话...');
    
    console.log('对话已重置');
}

function testLLM() {
    if (!websocket || websocket.readyState !== WebSocket.OPEN) {
        alert('请先开始持续对话模式');
        return;
    }
    
    // 更新状态显示
    $('#status').text('🧪 正在测试LLM连接...');
    $('#currentText').text('发送测试请求到LLM服务器...');
    
    // 发送测试请求到后端
    const testMessage = {
        type: 'test_llm'
    };
    
    if (WebSocketManager.safeSend(websocket, JSON.stringify(testMessage))) {
        console.log('已发送LLM测试请求');
        
        // 添加测试开始的视觉反馈
        const $results = $('#results');
        const loadingHtml = `
            <div id="llm-test-loading" style="border: 2px solid #17a2b8; border-radius: 8px; margin: 15px 0; overflow: hidden;">
                <div style="background: #d1ecf1; padding: 10px; border-bottom: 1px solid #bee5eb;">
                    <strong>🧪 LLM连接测试</strong>
                    <span style="float: right; font-size: 12px; color: #0c5460;">
                        ${new Date().toLocaleTimeString()}
                    </span>
                </div>
                <div style="padding: 15px; text-align: center;">
                    <div style="color: #0c5460; margin-bottom: 10px;">⏳ 正在测试LLM连接...</div>
                    <div style="font-size: 12px; color: #666;">请稍等，正在验证AI服务可用性</div>
                </div>
            </div>
        `;
        $results.prepend(loadingHtml);
        DOMUtils.scrollToBottom();
    } else {
        $('#status').text('❌ 发送测试请求失败');
        $('#currentText').text('无法发送测试请求，请检查连接状态');
    }
}

function toggleTTS() {
    const enabled = TTSManager.toggleTTS();
    
    // 更新状态显示
    $('#status').text(enabled ? '🔊 TTS已启用' : '🔇 TTS已禁用');
    $('#currentText').text(enabled ? 'AI回答将自动播放语音' : 'AI回答仅显示文字');
    
    console.log(`TTS功能${enabled ? '已启用' : '已禁用'}`);
}

// ===========================
// 文件上传处理模块
// ===========================
const FileUploadManager = {
    /**
     * 批量识别音频文件
     */
    async uploadAudio() {
        const $fileInput = DOMUtils.getElement('audioFile');
        const file = $fileInput[0].files[0]; // jQuery对象转原生DOM访问files
        
        // 验证文件
        const validation = Utils.validateAudioFile(file);
        if (!validation.valid) {
            alert(validation.error);
            return;
        }
        
        console.log(`开始上传音频文件: ${file.name}, 大小: ${Utils.formatFileSize(file.size)}`);
        
        try {
            const result = await this.processFileUpload(file, this.showBatchProgress);
            
            // 延迟显示结果
            setTimeout(() => {
                this.displayBatchResult(result);
                this.clearFileInput();
            }, CONSTANTS.TIMINGS.PROGRESS_UPDATE_DELAY);
            
        } catch (error) {
            console.error('离线识别失败:', error);
            UIManager.showError(`❌ 处理失败: ${error.message}`);
        }
    },
    
    /**
     * 处理文件上传
     */
    async processFileUpload(file, progressCallback) {
        const formData = new FormData();
        formData.append('audio', file);
        
        const startTime = Date.now();
        progressCallback('📤 正在上传音频文件...', 0);
        
        try {
            const result = await $.ajax({
                url: '/api/recognize/',
                type: 'POST',
                data: formData,
                processData: false,
                contentType: false,
                timeout: 300000 // 5分钟超时
            });
            
            const uploadTime = Date.now() - startTime;
            console.log(`文件上传耗时: ${Utils.formatTime(uploadTime)}`);
            
            progressCallback('🔍 正在进行语音识别...', 50);
            
            if (!result.success) {
                throw new Error(result.error || '识别失败');
            }
            
            progressCallback('🤖 AI正在生成回复...', 90);
            
            return { ...result, uploadTime };
        } catch (xhr) {
            if (xhr.status) {
                throw new Error(`HTTP ${xhr.status}: ${xhr.statusText}`);
            } else {
                throw xhr;
            }
        }
    },
    
    /**
     * 显示批量处理进度
     */
    showBatchProgress(message, progress) {
        UIManager.showUploadProgress(message, progress);
    },
    
    /**
     * 显示批量处理结果
     */
    displayBatchResult(result) {
        UIManager.displayOfflineResult(
            result.text, 
            result.llm_response, 
            result.debug_info, 
            result.uploadTime
        );
    },
    
    /**
     * 清空文件输入
     */
    clearFileInput() {
        DOMUtils.getElement('audioFile').val('');
    }
};

async function uploadAudio() {
    return FileUploadManager.uploadAudio();
}

// ===========================
// UI管理模块
// ===========================
const UIManager = {
    /**
     * 显示加载状态
     */
    showLoading(message) {
        $('#results').html(`<div class="loading">${message}</div>`);
    },
    
    /**
     * 显示上传进度
     */
    showUploadProgress(message, progress) {
        const progressHtml = `
            <div class="loading" style="text-align: center; padding: 20px;">
                <div style="font-size: 16px; margin-bottom: 10px;">${message}</div>
                <div style="background: #e9ecef; border-radius: 10px; height: 8px; width: 300px; margin: 0 auto;">
                    <div style="background: linear-gradient(90deg, #007bff, #28a745); height: 100%; border-radius: 10px; width: ${progress}%; transition: width 0.3s ease;"></div>
                </div>
                <div style="font-size: 12px; color: #666; margin-top: 5px;">${progress}%</div>
            </div>
        `;
        $('#results').html(progressHtml);
    },
    
    /**
     * 显示错误信息
     */
    showError(message) {
        $('#results').html(`
            <div style="background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; border-radius: 5px; padding: 15px; margin: 10px 0;">
                ${message}
            </div>
        `);
    },
    
    /**
     * 显示离线识别结果
     */
    displayOfflineResult(userText, aiResponse, debugInfo, uploadTime) {
        const $results = DOMUtils.getElement('results');
        
        // 处理时间信息
        const timeInfo = uploadTime ? `处理耗时: ${Utils.formatTime(uploadTime)}` : '';
        
        const debugHtml = debugInfo ? `
            <div style="font-size: 12px; color: #666; margin-top: 10px; padding: 8px; background: #f8f9fa; border-radius: 4px;">
                <div style="font-weight: bold; margin-bottom: 5px;">📊 处理信息:</div>
                <div>📁 文件: ${debugInfo.filename || '未知'}</div>
                <div>📦 原始大小: ${(debugInfo.original_size / 1024).toFixed(1)}KB</div>
                <div>🔄 处理后: ${(debugInfo.processed_size / 1024).toFixed(1)}KB</div>
                <div>🎵 采样率: ${debugInfo.sample_rate}Hz</div>
                <div>⏱️ ${timeInfo}</div>
                <div>🎯 音频格式: ${debugInfo.audio_info?.format || '自动检测'}</div>
            </div>
        ` : '';
        
        const html = `
            <div style="border: 2px solid #28a745; border-radius: 8px; margin: 15px 0; overflow: hidden;">
                <div style="background: #d4edda; padding: 10px; border-bottom: 1px solid #c3e6cb;">
                    <strong>🎵 离线语音识别结果</strong>
                    <span style="float: right; font-size: 12px; color: #155724;">
                        ${new Date().toLocaleTimeString()}
                    </span>
                </div>
                <div class="message user-message">
                    <strong>👤 识别内容:</strong> ${userText || '⚠️ 无法识别音频内容'}
                </div>
                <div class="message ai-message">
                    <strong>🤖 AI回复: </strong><span class="ai-content">${(aiResponse || '⚠️ 无法生成回答').replace(/\n/g, '<br>')}</span>
                </div>
                ${debugHtml}
            </div>
        `;
        
        $results.html(html + $results.html());
        DOMUtils.scrollToBottom();
        
        console.log('离线识别完成');
    },
    
    /**
     * 创建消息元素
     */
    createMessageElement(content, type, timestamp = true) {
        const timeStr = timestamp ? `<span class="message-timestamp">${new Date().toLocaleTimeString()}</span>` : '';
        
        let messageHtml;
        if (type === 'user') {
            messageHtml = `<strong>👤 用户:</strong> ${content.replace(/\n/g, '<br>')}<div>${timeStr}</div>`;
        } else if (type === 'ai') {
            messageHtml = `<strong>🤖 AI: </strong><span class="ai-content">${content.replace(/\n/g, '<br>')}</span><div>${timeStr}</div>`;
        }
        
        return $(`<div class="message ${type}-message">${messageHtml}</div>`)[0];
    }
};

function showLoading(message) {
    return UIManager.showLoading(message);
}

function showUploadProgress(message, progress) {
    return UIManager.showUploadProgress(message, progress);
}

function showError(message) {
    return UIManager.showError(message);
}

// 音频配置相关函数
function toggleAudioConfig() {
    const panel = document.getElementById('audioConfigPanel');
    const btn = document.getElementById('configBtn');
    
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        btn.textContent = '隐藏设置';
        updateBandwidthInfo();
    } else {
        panel.style.display = 'none';
        btn.textContent = '音频设置';
    }
}

// ===========================
// 配置管理模块
// ===========================
const ConfigManager = {
    /**
     * 更新音频配置
     */
    updateAudioConfig() {
        const sampleRate = parseInt($('#sampleRateSelect').val());
        const sendInterval = parseInt($('#sendIntervalSelect').val());
        
        // 更新配置
        appState.updateAudioConfig({
            sampleRate,
            sendInterval
        });
        
        // 同步到兼容变量
        Object.assign(audioConfig, appState.audioConfig);
        
        this.updateBandwidthInfo();
        
        console.log('音频配置已更新:', appState.audioConfig);
        
        // 如果正在录音，提示用户重启
        if (isStreaming) {
            this.showConfigChangeNotification();
        }
    },
    
    /**
     * 显示配置更改通知
     */
    showConfigChangeNotification() {
        const $notification = $(`
            <div style="position: fixed; top: 20px; right: 20px; background: #ffc107; color: #856404; 
                        padding: 12px 16px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); 
                        z-index: 1000; font-size: 14px; max-width: 300px;">
                <strong>⚠️ 配置已更新</strong><br>
                需要重新开始对话以应用新设置
            </div>
        `);
        
        $('body').append($notification);
        
        // 3秒后自动消失，带淡出效果
        setTimeout(() => {
            $notification.fadeOut(300, function() {
                $(this).remove();
            });
        }, 3000);
    },
    
    /**
     * 更新带宽信息显示
     */
    updateBandwidthInfo() {
        const sampleRate = parseInt($('#sampleRateSelect').val());
        
        // 计算理论带宽: 采样率 * 2字节 (16位) * 1声道
        let bandwidth = sampleRate * 2; // bytes per second
        
        const bandwidthKB = (bandwidth / 1024).toFixed(1);
        const bandwidthMB = (bandwidth * 60 / 1024 / 1024).toFixed(1); // per minute
        
        $('#bandwidthInfo').text(`📈 预估带宽: ${bandwidthKB}KB/秒 (${bandwidthMB}MB/分钟)`);
    },
    
    /**
     * 切换音频配置面板显示
     */
    toggleAudioConfig() {
        const $panel = $('#audioConfigPanel');
        const $btn = $('#configBtn');
        
        if ($panel.is(':hidden')) {
            $panel.show();
            $btn.text('隐藏设置');
            this.updateBandwidthInfo();
        } else {
            $panel.hide();
            $btn.text('音频设置');
        }
    }
};

function updateAudioConfig() {
    return ConfigManager.updateAudioConfig();
}

function updateBandwidthInfo() {
    return ConfigManager.updateBandwidthInfo();
}

function toggleAudioConfig() {
    return ConfigManager.toggleAudioConfig();
}

// 文件拖拽相关函数
function handleFileSelect(event) {
    const file = event.target.files[0];
    if (file) {
        displayFileInfo(file);
    }
}

function handleDragOver(event) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
}

function handleDragEnter(event) {
    event.preventDefault();
    $('#uploadArea').addClass('dragover');
}

function handleDragLeave(event) {
    event.preventDefault();
    $('#uploadArea').removeClass('dragover');
}

function handleFileDrop(event) {
    event.preventDefault();
    $('#uploadArea').css({
        backgroundColor: '#f8f9fa',
        borderColor: '#007bff'
    });
    
    const files = event.dataTransfer.files;
    if (files.length > 0) {
        const file = files[0];
        document.getElementById('audioFile').files = files;
        displayFileInfo(file);
    }
}

function displayFileInfo(file) {
    const $fileInfoContainer = $('#fileInfoContainer');
    const $offlineButtons = $('#offlineButtons');
    
    if (!file) {
        $fileInfoContainer.empty();
        $offlineButtons.hide();
        return;
    }

    const fileInfo = `
        <div style="background: #e3f2fd; border-left: 4px solid #2196f3; border-radius: 4px; padding: 10px; text-align: left;">
            <strong>已选文件:</strong>
            <div style="font-family: monospace; margin-top: 5px;">${file.name}</div>
            <div style="color: #666; font-size: 12px; margin-top: 3px;">
                大小: ${(file.size / 1024 / 1024).toFixed(2)}MB | 类型: ${file.type || '未知'}
            </div>
        </div>
    `;
    $fileInfoContainer.html(fileInfo);
    $offlineButtons.show();

    console.log('选择了文件:', {
        name: file.name,
        size: file.size,
        type: file.type
    });
}

// 流式上传识别功能
async function streamUploadAudio() {
    const file = $('#audioFile')[0].files[0];
    
    if (!file) {
        alert('📁 请先选择一个音频文件');
        return;
    }
    
    console.log(`开始流式识别: ${file.name}, 大小: ${(file.size/1024/1024).toFixed(2)}MB`);
    
    // 显示流式识别界面
    showStreamRecognition();
    
    try {
        // 建立WebSocket连接
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/upload`;
        const ws = new WebSocket(wsUrl);
        
        ws.onopen = async () => {
            console.log('WebSocket连接已建立');
            updateStreamStatus('📡 连接已建立，开始上传音频文件...');
            
            // 发送音频文件
            const arrayBuffer = await file.arrayBuffer();
            ws.send(arrayBuffer);
        };
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            handleStreamMessage(data);
        };
        
        ws.onclose = (event) => {
            console.log('WebSocket连接已关闭');
            if (event.code !== 1000) {
                updateStreamStatus('❌ 连接异常关闭');
            }
        };
        
        ws.onerror = (error) => {
            console.error('WebSocket错误:', error);
            updateStreamStatus('❌ 连接错误');
        };
        
    } catch (error) {
        console.error('流式识别失败:', error);
        updateStreamStatus(`❌ 失败: ${error.message}`);
    }
}

function showStreamRecognition() {
    const streamHtml = `
        <div id="streamContainer" style="border: 2px solid #007bff; border-radius: 8px; margin: 15px 0; overflow: hidden;">
            <div style="background: #cce5ff; padding: 10px; border-bottom: 1px solid #99ccff;">
                <strong>🌊 流式语音识别</strong>
                <span style="float: right; font-size: 12px; color: #0056b3;">
                    ${new Date().toLocaleTimeString()}
                </span>
            </div>
            
            <div style="padding: 15px;">
                <div id="streamStatus" style="color: #007bff; margin-bottom: 10px;">准备中...</div>
                
                <!-- 进度条 -->
                <div id="progressContainer" style="margin: 10px 0;">
                    <div style="background: #e9ecef; border-radius: 10px; height: 6px; overflow: hidden;">
                        <div id="progressBar" style="background: linear-gradient(90deg, #007bff, #28a745); height: 100%; width: 0%; transition: width 0.3s ease;"></div>
                    </div>
                    <div id="progressText" style="font-size: 12px; color: #666; margin-top: 5px;">0%</div>
                </div>
                
                <!-- 识别结果区域 -->
                <div id="recognitionResults" style="background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 4px; padding: 10px; margin: 10px 0; min-height: 50px;">
                    <div style="color: #666; font-style: italic;">等待识别结果...</div>
                </div>
                
                <!-- LLM回复区域 -->
                <div id="llmResults" style="background: #f3e5f5; border: 1px solid #d1c4e9; border-radius: 4px; padding: 10px; margin: 10px 0; min-height: 50px; display: none;">
                    <div style="font-weight: bold; color: #7b1fa2; margin-bottom: 5px;">🤖 AI回复: </div>
                    <div id="llmContent"></div>
                </div>
            </div>
        </div>
    `;
    
    $('#results').prepend(streamHtml);
    scrollToBottom();
}

function updateStreamStatus(message) {
    $('#streamStatus').text(message);
}

function updateStreamProgress(progress) {
    $('#progressBar').css('width', `${progress}%`);
    $('#progressText').text(`${progress.toFixed(1)}%`);
}

function updateRecognitionDisplay(newSegment, partialText, accumulatedText, isPartialUpdate = false) {
    const $resultsDiv = $('#recognitionResults');
    if (!$resultsDiv.length) return;
    
    // 获取当前的确认累积文本
    let confirmedAccumulated = '';
    const $existingConfirmed = $resultsDiv.find('[data-confirmed-accumulated]');
    if ($existingConfirmed.length) {
        confirmedAccumulated = $existingConfirmed.text();
    }
    
    // 如果传入了服务器确认的完整累积文本，使用它
    if (accumulatedText !== null && accumulatedText !== undefined) {
        confirmedAccumulated = accumulatedText;
    }
    
    // 如果有新确认片段，添加到确认累积文本中
    if (newSegment && newSegment.trim() && !isPartialUpdate) {
        confirmedAccumulated += newSegment;
    }
    
    // 计算当前显示的完整文本
    let currentDisplayText = confirmedAccumulated;
    let partialDisplayText = '';
    
    // 如果有实时识别文本，添加到显示中
    if (partialText && partialText.trim()) {
        partialDisplayText = partialText;
        currentDisplayText = confirmedAccumulated + partialText;
    }
    
    // 构建显示内容
    let displayHtml = '';
    
    // 显示累积结果（常驻）
    if (currentDisplayText || confirmedAccumulated) {
        displayHtml += `
            <div style="margin-bottom: 10px;">
                <div style="font-weight: bold; color: #28a745; margin-bottom: 5px;">📝 累积识别结果:</div>
                <div data-accumulated style="line-height: 1.6; padding: 8px; background: #f8f9fa; border-left: 3px solid #28a745;">
                    <span data-confirmed-accumulated>${confirmedAccumulated}</span><span style="color: #007bff; background: #e3f2fd; padding: 0 2px; border-radius: 2px;">${partialDisplayText}</span>
                </div>
            </div>
        `;
    }
    
    // 显示新片段（如果有，仅临时显示）
    if (newSegment && newSegment.trim()) {
        const alertId = 'newSegmentAlert_' + Date.now();
        displayHtml += `
            <div style="margin-bottom: 10px;" id="${alertId}">
                <div style="font-weight: bold; color: #17a2b8; margin-bottom: 5px;">
                    <span style="background: #d1ecf1; padding: 2px 6px; border-radius: 3px; font-size: 12px;">🆕 新识别片段</span>
                </div>
                <div style="color: #17a2b8; font-style: italic; padding: 8px; background: #d1ecf1; border-left: 3px solid #17a2b8;">
                    ${newSegment}
                </div>
            </div>
        `;
        
        // 3秒后隐藏新片段提示，使用jQuery动画
        setTimeout(() => {
            $(`#${alertId}`).fadeOut(500, function() {
                $(this).remove();
            });
        }, 3000);
    }
    
    // 显示实时识别（临时）
    if (partialText && partialText.trim()) {
        displayHtml += `
            <div style="margin-bottom: 10px;">
                <div style="font-weight: bold; color: #007bff; margin-bottom: 5px;">⚡ 实时识别:</div>
                <div style="color: #007bff; font-style: italic; padding: 8px; background: #cce5ff; border-left: 3px solid #007bff;">
                    ${partialText}
                </div>
            </div>
        `;
    }
    
    // 如果没有任何内容，显示等待状态
    if (!displayHtml) {
        displayHtml = '<div style="color: #666; font-style: italic;">等待识别结果...</div>';
    }
    
    $resultsDiv.html(displayHtml);
    scrollToBottom();
}

function handleStreamMessage(data) {
    console.log('收到流式消息:', data.type, data);
    
    switch (data.type) {
        case 'connected':
            updateStreamStatus('✅ ' + data.message);
            break;
            
        case 'file_received':
            updateStreamStatus(`📁 文件接收完成 (${(data.size/1024/1024).toFixed(2)}MB)`);
            break;
            
        case 'processing':
            updateStreamStatus('🔄 ' + data.message);
            break;
            
        case 'recognition_start':
            updateStreamStatus('🎤 ' + data.message);
            // 初始化识别结果显示
            updateRecognitionDisplay(null, null, '', false);
            break;
            
        case 'upload_progress':
            updateStreamProgress(data.progress);
            updateStreamStatus(`📤 音频上传中... ${data.current}/${data.total}`);
            break;
            
        case 'upload_complete':
            updateStreamProgress(100);
            updateStreamStatus('✅ ' + data.message);
            break;
            
        case 'recognition_partial':
            // 实时识别结果立即追加到累积结果中
            updateRecognitionDisplay(null, data.text, null, true);
            break;
            
        case 'recognition_segment':
            // 收到确认片段时，确认累积结果
            updateRecognitionDisplay(data.text, null, data.accumulated, false);
            
            // 根据模式显示不同的状态
            if (data.mode === 'offline') {
                updateStreamStatus('🎯 离线识别进行中...');
            } else {
                updateStreamStatus('🎯 识别进行中...');
            }
            scrollToBottom();
            break;
            
        case 'llm_start':
            updateStreamStatus('🤖 ' + data.message);
            const $llmDiv = $('#llmResults');
            if ($llmDiv.length) {
                $llmDiv.show();
                $('#llmContent').empty();
            }
            break;
            
        case 'llm_chunk':
            console.log('收到LLM chunk:', data);
            
            // 检查chunk是否为有效字符串
            let chunkContent = data.chunk;
            if (chunkContent === undefined || chunkContent === null || chunkContent === '') {
                console.warn('收到空的chunk:', data);
                break;
            } else {
                chunkContent = String(chunkContent); // 确保是字符串
            }
            
            // 确保LLM容器存在
            let $llmContent = $('#llmContent');
            if (!$llmContent.length) {
                const $resultsDiv = $('#results');
                if ($resultsDiv.length && !$('#llmResults').length) {
                    $resultsDiv.append(`
                        <div id="llmResults" style="margin-top: 15px;">
                            <div style="font-weight: bold; color: #6f42c1; margin-bottom: 5px;">🤖 AI回复: </div>
                            <div id="llmContent" style="padding: 10px; background: #f8f9fa; border-left: 3px solid #6f42c1; white-space: pre-wrap;"></div>
                        </div>
                    `);
                    $llmContent = $('#llmContent');
                }
            }
            
            // 确保有容器才添加内容
            if ($llmContent.length) {
                // 直接追加文本内容，让浏览器自动处理换行
                const currentText = $llmContent.text();
                $llmContent.text(currentText + chunkContent);
                scrollToBottom();
            }
            break;
            
        case 'llm_complete':
            updateStreamStatus('✅ AI回复完成');
            console.log('LLM回复完成:', {
                recognized_text: data.recognized_text,
                llm_response: data.llm_response
            });
            
            scrollToBottom();
            
            // 清空文件选择
            $('#audioFile').val('');
            break;
            
        case 'complete':
            updateStreamStatus('✅ 处理完成');
            console.log('流式识别完成:', {
                recognition: data.recognition_result,
                llm: data.llm_response
            });
            scrollToBottom();
            
            // 清空文件选择
            $('#audioFile').val('');
            break;
            
        case 'error':
        case 'llm_error':
            updateStreamStatus('❌ ' + data.message);
            break;
            
        default:
            console.log('未知消息类型:', data.type);
    }
}

function switchMode(mode) {
    const $realtimeControls = $('#realtimeControls');
    const $offlineControls = $('#offlineControls');
    const $realtimeModeBtn = $('#realtimeModeBtn');
    const $offlineModeBtn = $('#offlineModeBtn');
    const $currentText = $('#currentText');

    if (mode === 'realtime') {
        $realtimeControls.show();
        $offlineControls.hide();
        $realtimeModeBtn.addClass('active');
        $offlineModeBtn.removeClass('active');
        $currentText.text('等待开始对话...');
    } else { // offline mode
        $realtimeControls.hide();
        $offlineControls.show();
        $realtimeModeBtn.removeClass('active');
        $offlineModeBtn.addClass('active');
        $currentText.text('请上传音频文件...');
    }
}

// ===========================
// 流式显示效果
// ===========================

/**
 * 简单流式显示 - 支持换行符
 */
function addTypingEffect($element, text) {
    // 获取当前HTML内容，处理换行符
    const currentHtml = $element.html();
    const newText = text.replace(/\n/g, '<br>');
    $element.html(currentHtml + newText);
    
    scrollToBottom();
}





// ===========================
// 应用初始化
// ===========================
$(document).ready(async function() {
    console.log('🚀 应用正在初始化...');
    
    // 获取后端配置
    await AppInitializer.fetchConfig();
    
    // 初始化UI状态
    updateMemoryStatus();
    updateUserInfo(null, 0);
    
    // 启动在线用户数更新器
    startOnlineUsersUpdater();
    
    // 添加CSS动画
    $('<style>').text(`
        @keyframes blink {
            0%, 50% { opacity: 1; }
            51%, 100% { opacity: 0; }
        }
        
        .typing-effect {
            border-right: 2px solid #6f42c1;
            animation: blink 1s infinite;
        }
    `).appendTo('head');
    
    console.log('✅ 应用初始化完成，jQuery已加载');
});

// ===========================
// TTS音频播放管理器
// ===========================
const TTSManager = {
    audioContext: null,
    audioQueue: [],
    isPlaying: false,
    
    /**
     * 初始化音频上下文
     */
    async initAudioContext() {
        if (!this.audioContext) {
            try {
                this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
                console.log('🔊 TTS音频上下文初始化成功');
            } catch (error) {
                console.error('初始化音频上下文失败:', error);
                return false;
            }
        }
        
        // 确保音频上下文处于运行状态
        if (this.audioContext.state === 'suspended') {
            await this.audioContext.resume();
        }
        
        return true;
    },
    
    /**
     * 播放Base64编码的音频数据
     */
    async playAudioData(audioBase64, sampleRate = 22050, format = 'pcm') {
        if (!appState.ttsEnabled) {
            console.log('TTS已禁用，跳过音频播放');
            return;
        }
        
        try {
            // 初始化音频上下文
            if (!(await this.initAudioContext())) {
                return;
            }
            
            // 解码Base64音频数据
            const audioData = this.base64ToArrayBuffer(audioBase64);
            
            if (format === 'pcm') {
                // 处理PCM格式的音频数据
                await this.playPCMAudio(audioData, sampleRate);
            } else {
                // 处理其他格式的音频数据
                await this.playDecodedAudio(audioData);
            }
            
        } catch (error) {
            console.error('播放TTS音频失败:', error);
        }
    },
    
    /**
     * 播放PCM格式的音频数据
     */
    async playPCMAudio(arrayBuffer, sampleRate) {
        try {
            // 将ArrayBuffer转换为Float32Array
            const int16Array = new Int16Array(arrayBuffer);
            const float32Array = new Float32Array(int16Array.length);
            
            // 转换16位整数到浮点数 (-1.0 到 1.0)
            for (let i = 0; i < int16Array.length; i++) {
                float32Array[i] = int16Array[i] / 32768.0;
            }
            
            // 创建音频缓冲区
            const audioBuffer = this.audioContext.createBuffer(1, float32Array.length, sampleRate);
            audioBuffer.getChannelData(0).set(float32Array);
            
            // 播放音频
            await this.playAudioBuffer(audioBuffer);
            
        } catch (error) {
            console.error('播放PCM音频失败:', error);
        }
    },
    
    /**
     * 播放已解码的音频数据
     */
    async playDecodedAudio(arrayBuffer) {
        try {
            const audioBuffer = await this.audioContext.decodeAudioData(arrayBuffer);
            await this.playAudioBuffer(audioBuffer);
        } catch (error) {
            console.error('解码音频数据失败:', error);
        }
    },
    
    /**
     * 播放音频缓冲区
     */
    async playAudioBuffer(audioBuffer) {
        return new Promise((resolve) => {
            const source = this.audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(this.audioContext.destination);
            
            source.onended = () => {
                resolve();
            };
            
            source.start();
        });
    },
    
    /**
     * Base64转ArrayBuffer
     */
    base64ToArrayBuffer(base64) {
        const binaryString = window.atob(base64);
        const len = binaryString.length;
        const bytes = new Uint8Array(len);
        
        for (let i = 0; i < len; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        
        return bytes.buffer;
    },
    
    /**
     * 停止所有TTS播放
     */
    stopAll() {
        this.audioQueue = [];
        this.isPlaying = false;
        
        if (this.audioContext) {
            // 停止所有音频节点
            this.audioContext.suspend();
        }
        
        console.log('🔇 已停止所有TTS播放');
    },
    
    /**
     * 切换TTS启用状态
     */
    toggleTTS() {
        appState.ttsEnabled = !appState.ttsEnabled;
        console.log(`🔊 TTS ${appState.ttsEnabled ? '已启用' : '已禁用'}`);
        
        if (!appState.ttsEnabled) {
            this.stopAll();
        }
        
        // 更新UI状态
        this.updateTTSButton();
        
        return appState.ttsEnabled;
    },
    
    /**
     * 更新TTS按钮状态
     */
    updateTTSButton() {
        const $ttsBtn = $('#ttsBtn');
        if ($ttsBtn.length) {
            $ttsBtn.text(appState.ttsEnabled ? '🔊 TTS开启' : '🔇 TTS关闭')
                   .toggleClass('active', appState.ttsEnabled);
        }
    }
}; 