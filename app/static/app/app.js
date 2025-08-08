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
        SEND_INTERVAL: 50  // 优化为50ms提高实时性
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
        TTS_INTERRUPT: 'tts_interrupt',
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
                status: getLangText('listening')
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

            // 添加连接状态监控
            const statusInterval = setInterval(() => {
                if (ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
                    clearInterval(statusInterval);
                    console.warn('🔌 WebSocket连接已断开');
                }
            }, 5000); // 每5秒检查一次

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
                        status: getLangText('connectionClosed')
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
        const langData = LANGUAGE_DATA[currentLanguage] || LANGUAGE_DATA['en'];
        $btn.text(langData.connecting).prop('disabled', true);
        $status.text(getLangText('connectingWs'));

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
                $status.text(getLangText('connectionFailed'));
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
            $status.text(getLangText('asrConnecting'));

            // 3. 等待后端发送ASR连接确认
            await asrConnectionPromise;

            // 清除超时定时器
            clearTimeout(connectionTimeout);

            // 4. ASR连接成功，启动麦克风
                            $status.text(getLangText('micStarting'));

            try {
                await startContinuousRecording();

                // 只有在成功启动麦克风后才切换为"停止对话"状态
                const langData = LANGUAGE_DATA[currentLanguage] || LANGUAGE_DATA['en'];
            $btn.text(langData.stopChat).addClass('active').prop('disabled', false);
                $status.text(getLangText('listening'));
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
                        .text(LANGUAGE_DATA[currentLanguage]?.startContinuousChat || LANGUAGE_DATA['en'].startContinuousChat)
        .removeClass('active')
        .prop('disabled', false);

    $('#status').text(getLangText('disconnected'));
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
            // 检查WebSocket连接状态
            if (!websocket || websocket.readyState !== WebSocket.OPEN) {
                console.warn('⚠️ WebSocket连接不可用，跳过音频数据发送');
                return;
            }

            // 缓冲数据
            this.audioBuffer = this.combineArrays(this.audioBuffer, newData);

            // 按间隔发送，减少网络频率
            const now = Date.now();
            if (now - this.lastSendTime >= appState.audioConfig.sendInterval && this.audioBuffer.length > 0) {
                const dataToSend = this.prepareDataForSending();

                try {
                    websocket.send(dataToSend.buffer);
                    this.audioBuffer = new Int16Array(0);  // 清空缓冲
                    this.lastSendTime = now;
                } catch (sendError) {
                    console.error('❌ 发送音频数据失败:', sendError);
                    // 不清空缓冲，下次重试
                }
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
    // 更新状态
    isStreaming = false;
    conversationCount = 0;
    currentUserId = null;

    // 重置按钮到默认状态
    resetButtonToDefault();

    // 使用jQuery链式调用优雅地更新UI
            $('#currentText').text(getLangText('waitingToStart')).removeClass('partial-text');

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

            // 开始新的TTS播放
            TTSManager.startNewTTS();

            DOMUtils.updateTexts({
                status: '🔊 正在合成语音...'
            });
        },

        'tts_audio': function(data) {
            TTSManager.playAudioData(data.audio_data, data.sample_rate, data.format);
        },

        'tts_complete': function(data) {
            console.log('TTS合成完成:', data.message);

            // 短暂显示完成状态
            DOMUtils.updateTexts({
                status: '✅ 语音合成完成'
            });

            // 2秒后恢复到监听状态（如果还在流式模式）
            setTimeout(() => {
                if (isStreaming) {
                    DOMUtils.updateTexts({
                        status: getLangText('listening')
                    });
                }
            }, 2000);

            console.log('🎵 TTS播放已结束，状态即将恢复到监听模式');
        },

        'tts_error': function(data) {
            console.error('TTS合成错误:', data.error);

            // 停止所有TTS播放
            TTSManager.stopAll();

            // 恢复状态，让用户知道可以继续对话
            DOMUtils.updateTexts({
                status: '⚠️ 语音合成失败，但可以继续对话'
            });

            // 3秒后恢复正常状态
            setTimeout(() => {
                if (isStreaming) {
                    DOMUtils.updateTexts({
                        status: getLangText('listening')
                    });
                }
            }, 3000);

            ErrorHandler.showError(data.error, 'TTS错误');
        },

        'tts_interrupt': function(data) {
            console.log('收到TTS中断信号:', data.reason);

            // 立即停止TTS播放
            TTSManager.stopAll();

            DOMUtils.updateTexts({
                status: `🛑 TTS播放已中断: ${data.reason}`
            });
        },



        'asr_error': function(data) {
            console.warn('⚠️ ASR服务错误:', data.message);

            DOMUtils.updateTexts({
                status: `⚠️ ${data.message}`
            });

            // 显示用户友好的错误提示
            if (isStreaming) {
                setTimeout(() => {
                    DOMUtils.updateTexts({
                        status: getLangText('listening')
                    });
                }, 3000); // 3秒后恢复正常状态显示
            }
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
        },

        'ai_response_complete': function(data) {
            console.log('AI回答完成（含TTS处理）:', data.message);

            // 完成AI回答（这个可以立即执行，表示AI文本回答完成）
            this.completeAIResponse();

            console.log('📝 AI文本回答已完成，TTS继续播放中...');
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
                            status: getLangText('aiThinking'),
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

            $('#status').text(getLangText('aiResponding'));
        } else {
            console.error('currentAiMessage为null，无法添加chunk');
        }
    },

    /**
     * 完成AI响应
     */
    completeAIResponse() {
        $('#status').text(getLangText('listening'));
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
            $('#status').text(getLangText('listening'));
            $('#currentText').text(getLangText('waitingNextSentence'));
        } else {
            $('#status').text(getLangText('disconnected'));
            $('#currentText').text(getLangText('waitingToStart'));
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
    const langData = LANGUAGE_DATA[currentLanguage] || LANGUAGE_DATA['en'];

    if (conversationCount === 0) {
        if (currentLanguage === 'zh') {
            $memoryStatus.text('🧠 AI记忆: 空白状态');
        } else {
            $memoryStatus.text('🧠 AI Memory: Empty');
        }
    } else if (conversationCount === 1) {
        if (currentLanguage === 'zh') {
            $memoryStatus.text('🧠 AI记忆: 记住1轮对话');
        } else {
            $memoryStatus.text('🧠 AI Memory: 1 conversation');
        }
    } else if (conversationCount < maxHistory) {
        if (currentLanguage === 'zh') {
            $memoryStatus.text(`🧠 AI记忆: 记住${conversationCount}轮对话`);
        } else {
            $memoryStatus.text(`🧠 AI Memory: ${conversationCount} conversations`);
        }
    } else {
        if (currentLanguage === 'zh') {
            $memoryStatus.text(`🧠 AI记忆: 记住最近${maxHistory}轮对话`);
        } else {
            $memoryStatus.text(`🧠 AI Memory: Recent ${maxHistory} conversations`);
        }
    }
}

function updateUserInfo(userId, activeUsers) {
    const $userInfo = $('#userInfo');
    if (userId) {
        const shortId = userId.substring(0, 8) + '...';
        if (currentLanguage === 'zh') {
            $userInfo.text(`👤 用户: ${shortId} | 🌐 在线: ${activeUsers}人`);
        } else {
            $userInfo.text(`👤 User: ${shortId} | 🌐 Online: ${activeUsers}`);
        }

        // 从连接池获取准确的在线人数
        fetchAccurateOnlineUsers();
    } else {
        if (currentLanguage === 'zh') {
            $userInfo.text('👤 用户: 未连接 | 🌐 在线: 0人');
        } else {
            $userInfo.text('👤 User: Disconnected | 🌐 Online: 0');
        }
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

            if (currentLanguage === 'zh') {
                $userInfo.text(`${userPart} | 🌐 在线: ${activeUsers}人`);
                console.log(`从连接池获取准确在线人数: ${activeUsers}`);
            } else {
                $userInfo.text(`${userPart} | 🌐 Online: ${activeUsers}`);
                console.log(`Fetched accurate online users from pool: ${activeUsers}`);
            }
        }
    } catch (error) {
        if (currentLanguage === 'zh') {
            console.warn('获取连接池状态失败:', error);
        } else {
            console.warn('Failed to fetch pool stats:', error);
        }
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

            $('#currentText').text(isStreaming ? getLangText('waitingNextSentence') : getLangText('waitingToStart'));

    console.log('对话已重置');
}

function testLLM() {
    if (!websocket || websocket.readyState !== WebSocket.OPEN) {
        alert('请先开始持续对话模式');
        return;
    }

    // 更新状态显示
    $('#status').text(getLangText('testingLLM'));
    $('#currentText').text(getLangText('sendingTestRequest'));

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
        $('#status').text(getLangText('testRequestFailed'));
        $('#currentText').text(getLangText('testRequestFailedDetail'));
    }
}

function toggleTTS() {
    const enabled = TTSManager.toggleTTS();

    // 更新状态显示
    $('#status').text(enabled ? getLangText('ttsEnabled') : getLangText('ttsDisabled'));
    $('#currentText').text(enabled ? getLangText('ttsEnabledDetail') : getLangText('ttsDisabledDetail'));

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

// 音频配置相关函数 - 使用ConfigManager统一管理

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

        // 根据当前语言显示
        if (currentLanguage === 'zh') {
            $('#bandwidthInfo').text(`📈 预估带宽: ${bandwidthKB}KB/秒 (${bandwidthMB}MB/分钟)`);
        } else {
            $('#bandwidthInfo').text(`📈 Estimated Bandwidth: ${bandwidthKB}KB/sec (${bandwidthMB}MB/min)`);
        }
    },

    /**
     * 切换音频配置面板显示
     */
    toggleAudioConfig() {
        const $panel = $('#audioConfigPanel');
        const $btn = $('#configBtn');
        const langData = LANGUAGE_DATA[currentLanguage] || LANGUAGE_DATA['en'];

        if ($panel.is(':hidden')) {
            $panel.show();
            $btn.text(langData.hideSettings);
            this.updateBandwidthInfo();
        } else {
            $panel.hide();
            $btn.text(langData.audioSettings);
        }
    }
};

// 直接使用 ConfigManager.methodName() 调用

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
                <strong>${getLangText('streamRecognition')}</strong>
                <span style="float: right; font-size: 12px; color: #0056b3;">
                    ${new Date().toLocaleTimeString()}
                </span>
            </div>

            <div style="padding: 15px;">
                <div id="streamStatus" style="color: #007bff; margin-bottom: 10px;">${getLangText('preparing')}</div>

                <!-- 进度条 -->
                <div id="progressContainer" style="margin: 10px 0;">
                    <div style="background: #e9ecef; border-radius: 10px; height: 6px; overflow: hidden;">
                        <div id="progressBar" style="background: linear-gradient(90deg, #007bff, #28a745); height: 100%; width: 0%; transition: width 0.3s ease;"></div>
                    </div>
                    <div id="progressText" style="font-size: 12px; color: #666; margin-top: 5px;">0%</div>
                </div>

                <!-- 识别结果区域 -->
                <div id="recognitionResults" style="background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 4px; padding: 10px; margin: 10px 0; min-height: 50px;">
                    <div style="color: #666; font-style: italic;">${getLangText('waitingRecognitionResults')}</div>
                </div>

                <!-- LLM回复区域 -->
                <div id="llmResults" style="background: #f3e5f5; border: 1px solid #d1c4e9; border-radius: 4px; padding: 10px; margin: 10px 0; min-height: 50px; display: none;">
                    <div style="font-weight: bold; color: #7b1fa2; margin-bottom: 5px;">${getLangText('aiReply')}</div>
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
        $currentText.text(getLangText('waitingToStart'));
    } else { // offline mode
        $realtimeControls.hide();
        $offlineControls.show();
        $realtimeModeBtn.removeClass('active');
        $offlineModeBtn.addClass('active');
        $currentText.text(getLangText('uploadAudioFile'));
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
            // console.log('🚀 应用正在初始化...'); // 减少调试日志

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
});

// ===========================
// TTS音频播放管理器
// ===========================
const TTSManager = {
    audioContext: null,
    audioQueue: [],
    isPlaying: false,
    currentSources: [], // 当前播放的音频源
    isInterrupted: false, // 是否被中断
    audioBufferQueue: [], // 音频缓冲区队列
    isProcessingQueue: false, // 是否正在处理队列
    // 基于时间轴的播放排程
    nextStartTime: 0,
    initialBufferSec: 0.25,

    // 音频播放统计
    playbackStats: {
        totalChunks: 0,
        totalDuration: 0,
        averageChunkDuration: 0,
        lastUpdateTime: 0
    },

    /**
     * 更新播放统计信息
     */
    updatePlaybackStats(audioBuffer) {
        this.playbackStats.totalChunks++;
        this.playbackStats.totalDuration += audioBuffer.duration;
        this.playbackStats.averageChunkDuration = this.playbackStats.totalDuration / this.playbackStats.totalChunks;
        this.playbackStats.lastUpdateTime = Date.now();

        // 每10个音频片段输出一次统计信息
        if (this.playbackStats.totalChunks % 10 === 0) {
            console.log('📊 TTS播放统计:', {
                总片段数: this.playbackStats.totalChunks,
                总时长: this.playbackStats.totalDuration.toFixed(2) + '秒',
                平均片段时长: this.playbackStats.averageChunkDuration.toFixed(3) + '秒',
                队列长度: this.audioBufferQueue.length
            });
        }
    },

    /**
     * 初始化音频上下文
     */
    async initAudioContext() {
        if (!this.audioContext) {
            try {
                this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
            } catch (error) {
                console.error('初始化音频上下文失败:', error);
                return false;
            }
        }

        // 确保音频上下文处于运行状态
        if (this.audioContext.state === 'suspended') {
            await this.audioContext.resume();
        }

        return this.audioContext.state === 'running';
    },

    /**
     * 播放Base64编码的音频数据
     */
    async playAudioData(audioBase64, sampleRate = 22050, format = 'pcm') {

        if (!appState.ttsEnabled || this.isInterrupted) {
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
                // 创建音频缓冲区并加入队列
                const audioBuffer = await this.createPCMAudioBuffer(audioData, sampleRate);
                if (audioBuffer) {
                    this.audioBufferQueue.push(audioBuffer);
                    this.processAudioQueue(); // 基于时间轴的播放
                }
            } else {
                // 其他格式：先解码为 AudioBuffer，再进入队列统一排程
                try {
                    const decoded = await this.audioContext.decodeAudioData(audioData);
                    if (decoded) {
                        this.audioBufferQueue.push(decoded);
                        this.processAudioQueue();
                    }
                } catch (e) {
                    console.error('解码TTS音频失败:', e);
                }
            }

        } catch (error) {
            console.error('❌ 播放TTS音频失败:', error);
        }
    },

    /**
     * 创建PCM音频缓冲区（不立即播放）
     */
    async createPCMAudioBuffer(arrayBuffer, sampleRate) {
        try {
            // 验证音频数据
            if (!arrayBuffer || arrayBuffer.byteLength === 0) {
                console.warn('⚠️ 收到空的音频数据');
                return null;
            }

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

            return audioBuffer;

        } catch (error) {
            console.error('❌ 创建PCM音频缓冲区失败:', error);
            return null;
        }
    },

    /**
     * 处理音频队列，实现无缝播放
     */
    async processAudioQueue() {
        if (this.isProcessingQueue || this.audioBufferQueue.length === 0) {
            return;
        }

        this.isProcessingQueue = true;
        try {
            while (this.audioBufferQueue.length > 0 && !this.isInterrupted) {
                const audioBuffer = this.audioBufferQueue.shift();
                // 更新播放统计
                this.updatePlaybackStats(audioBuffer);
                // 基于时间轴安排播放，避免依赖 onended 串播
                this.scheduleAudioBuffer(audioBuffer);
            }
        } finally {
            this.isProcessingQueue = false;
        }
    },

    /**
     * 基于AudioContext时间轴的无缝排程
     */
    scheduleAudioBuffer(audioBuffer) {
        try {
            if (!this.audioContext) return;

            const source = this.audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(this.audioContext.destination);

            // 注册到当前播放集合
            this.currentSources.push(source);
            this.isPlaying = true;

            source.onended = () => {
                const index = this.currentSources.indexOf(source);
                if (index > -1) this.currentSources.splice(index, 1);
                if (this.currentSources.length === 0) this.isPlaying = false;
            };

            const now = this.audioContext.currentTime;
            if (this.nextStartTime === 0 || this.nextStartTime < now) {
                // 初始化或追赶时，给出轻微预缓冲
                const lead = this.initialBufferSec;
                this.nextStartTime = Math.max(now + lead, now + 0.02);
            }
            const startAt = Math.max(this.nextStartTime, now + 0.005);
            source.start(startAt);
            this.nextStartTime = startAt + audioBuffer.duration;

        } catch (err) {
            console.error('❌ 排程TTS音频失败:', err);
        }
    },

    /**
     * 播放已解码的音频数据
     */
    async playDecodedAudio(arrayBuffer) {
        try {
            const audioBuffer = await this.audioContext.decodeAudioData(arrayBuffer);
            if (audioBuffer) {
                this.audioBufferQueue.push(audioBuffer);
                this.processAudioQueue();
            }
        } catch (error) {
            console.error('解码音频数据失败:', error);
        }
    },

    /**
     * 播放音频缓冲区
     */
    async playAudioBuffer(audioBuffer) {
        return new Promise((resolve) => {
            if (this.isInterrupted) {
                resolve();
                return;
            }

            // 确保音频上下文处于运行状态
            if (this.audioContext.state !== 'running') {
                this.audioContext.resume().then(() => {
                    // 音频上下文恢复后继续播放
                    this._startAudioPlayback(audioBuffer, resolve);
                }).catch(error => {
                    console.error('❌ 恢复音频上下文失败:', error);
                    resolve();
                });
            } else {
                this._startAudioPlayback(audioBuffer, resolve);
            }
        });
    },

    /**
     * 开始音频播放的内部方法
     */
    _startAudioPlayback(audioBuffer, resolve) {
        try {

            const source = this.audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(this.audioContext.destination);

            // 记录当前播放的音频源
            this.currentSources.push(source);
            this.isPlaying = true;

            source.onended = () => {

                // 从当前播放列表中移除
                const index = this.currentSources.indexOf(source);
                if (index > -1) {
                    this.currentSources.splice(index, 1);
                }

                // 如果没有正在播放的音频，更新状态
                if (this.currentSources.length === 0) {
                    this.isPlaying = false;
                }

                resolve();
            };

            // 开始播放
            source.start();

        } catch (error) {
            console.error('❌ 音频播放失败:', error);
            resolve();
        }
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
        this.isInterrupted = true;
        this.audioQueue = [];
        this.audioBufferQueue = []; // 清空音频缓冲区队列
        this.isProcessingQueue = false; // 停止队列处理
        this.nextStartTime = 0; // 重置时间轴

        // 重置播放统计
        this.playbackStats = {
            totalChunks: 0,
            totalDuration: 0,
            averageChunkDuration: 0,
            lastUpdateTime: 0
        };

        // 停止所有当前播放的音频源
        this.currentSources.forEach(source => {
            try {
                source.stop();
            } catch (e) {
                // 忽略已经停止的音频源错误
            }
        });
        this.currentSources = [];
        this.isPlaying = false;

        if (this.audioContext) {
            // 暂停音频上下文
            this.audioContext.suspend();
        }
    },

    /**
     * 开始新的TTS播放（会自动停止之前的播放）
     */
    startNewTTS() {
        this.stopAll();
        this.isInterrupted = false;
        this.audioBufferQueue = []; // 确保队列为空
        this.isProcessingQueue = false;
        this.nextStartTime = 0; // 重置时间轴

        // 恢复音频上下文
        if (this.audioContext && this.audioContext.state === 'suspended') {
            this.audioContext.resume();
        }
    },

    /**
     * 切换TTS启用状态
     */
    toggleTTS() {
        appState.ttsEnabled = !appState.ttsEnabled;

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
            // 根据当前语言获取对应文本
            const langData = LANGUAGE_DATA[currentLanguage] || LANGUAGE_DATA['en'];
            $ttsBtn.text(appState.ttsEnabled ? langData.ttsOn : langData.ttsOff)
                   .toggleClass('active', appState.ttsEnabled);
        }
    },



};

// ===========================
// 国际化/语言切换功能
// ===========================

// 语言数据
const LANGUAGE_DATA = {
    zh: {
        // 页面标题和标头（从后端配置读取）
        pageTitle: window.APP_CONFIG ? window.APP_CONFIG.pageTitleZh : 'Yoswit实时智能语音对话',
        mainTitle: window.APP_CONFIG ? window.APP_CONFIG.mainTitleZh : 'Yoswit实时智能语音助手',

        // 模式按钮
        realtimeMode: '🎙️ 实时对话',
        offlineMode: '📁 文件识别',

        // 控制按钮
        startChat: '开始对话',
        resetChat: '重置对话',
        testLLM: '测试LLM',
        audioSettings: '音频设置',
        hideSettings: '隐藏设置',
        ttsOn: '🔊 TTS开启',
        ttsOff: '🔇 TTS关闭',

        // 音频配置
        audioConfig: '🔧 音频配置',
        sampleRate: '📊 采样率:',
        sendInterval: '⏱️ 发送间隔:',
        phoneQuality: '电话质量',
        standardQuality: '标准质量',
        highQuality: '高质量',
        goodRealtime: '实时性好',
        balanced: '平衡',
        bandwidthSaving: '省带宽',
        estimatedBandwidth: '📈 预估带宽: 32KB/秒',

        // 文件上传
        uploadAudioFile: '上传音频文件',
        dragFilesHere: '📥 拖拽文件到此或点击选择',
        selectFile: '选择文件',
        batchRecognition: '🚀 批量识别',
        streamRecognition: '⚡ 流式识别',
        fileFormatsSupport: '支持WAV, MP3, M4A等格式，限制10MB',

        // 状态信息
        disconnected: '未连接',
        connecting: '连接中...',
        connectingWs: '正在连接WebSocket...',
        asrConnecting: '正在连接ASR服务器...',
        listening: '🎤 正在监听...',
        processing: '🔄 处理中...',
        aiThinking: '🤖 AI正在思考...',
        aiResponding: '🤖 AI正在回答...',
        micStarting: '正在启动麦克风...',
        micFailed: '麦克风启动失败',
        connectionFailed: '❌ ASR服务器连接失败',
        connectionClosed: '连接已断开',
        waitingOperation: '等待操作...',
        waitingToStart: '等待开始对话...',
        waitingNextSentence: '等待您的下一句话...',
        stopChat: '停止对话',
        startContinuousChat: '开始持续对话',
        testingLLM: '🧪 正在测试LLM连接...',
        sendingTestRequest: '发送测试请求到LLM服务器...',
        testRequestFailed: '❌ 发送测试请求失败',
        testRequestFailedDetail: '无法发送测试请求，请检查连接状态',
        ttsEnabled: '🔊 TTS已启用',
        ttsDisabled: '🔇 TTS已禁用',
        ttsEnabledDetail: 'AI回答将自动播放语音',
        ttsDisabledDetail: 'AI回答仅显示文字',
        uploadAudioFile: '请上传音频文件...',

        // 流式识别相关
        streamRecognition: '🌊 流式语音识别',
        preparing: '准备中...',
        waitingRecognitionResults: '等待识别结果...',
        aiReply: '🤖 AI回复: ',
        aiMemory: '🧠 AI记忆: 空白',
        aiMemoryEmpty: '空白',
        aiMemoryActive: '活跃',
        userInfo: '👤 用户ID: - | 🌐 在线: 0',
        userId: '用户ID',
        online: '在线',

        // 聊天结果区域
        chatResultsPlaceholder: '<!-- 聊天/识别结果将显示在这里 -->',

        // 语言切换
        currentLang: '中'
    },

    en: {
        // 页面标题和标头（从后端配置读取）
        pageTitle: window.APP_CONFIG ? window.APP_CONFIG.pageTitleEn : 'Yoswit Real-time AI Voice Chat',
        mainTitle: window.APP_CONFIG ? window.APP_CONFIG.mainTitleEn : 'Yoswit Real-time AI Voice Assistant',

        // 模式按钮
        realtimeMode: '🎙️ Real-time Chat',
        offlineMode: '📁 File Recognition',

        // 控制按钮
        startChat: 'Start Chat',
        resetChat: 'Reset Chat',
        testLLM: 'Test LLM',
        audioSettings: 'Audio Settings',
        hideSettings: 'Hide Settings',
        ttsOn: '🔊 TTS On',
        ttsOff: '🔇 TTS Off',

        // 音频配置
        audioConfig: '🔧 Audio Configuration',
        sampleRate: '📊 Sample Rate:',
        sendInterval: '⏱️ Send Interval:',
        phoneQuality: 'Phone Quality',
        standardQuality: 'Standard Quality',
        highQuality: 'High Quality',
        goodRealtime: 'Good Real-time',
        balanced: 'Balanced',
        bandwidthSaving: 'Bandwidth Saving',
        estimatedBandwidth: '📈 Estimated Bandwidth: 32KB/sec',

        // 文件上传
        uploadAudioFile: 'Upload Audio File',
        dragFilesHere: '📥 Drag files here or click to select',
        selectFile: 'Select File',
        batchRecognition: '🚀 Batch Recognition',
        streamRecognition: '⚡ Stream Recognition',
        fileFormatsSupport: 'Supports WAV, MP3, M4A formats, 10MB limit',

        // 状态信息
        disconnected: 'Disconnected',
        connecting: 'Connecting...',
        connectingWs: 'Connecting WebSocket...',
        asrConnecting: 'Connecting ASR Server...',
        listening: '🎤 Listening...',
        processing: '🔄 Processing...',
        aiThinking: '🤖 AI Thinking...',
        aiResponding: '🤖 AI Responding...',
        micStarting: 'Starting Microphone...',
        micFailed: 'Microphone Start Failed',
        connectionFailed: '❌ ASR Server Connection Failed',
        connectionClosed: 'Connection Closed',
        waitingOperation: 'Waiting for operation...',
        waitingToStart: 'Waiting to start chat...',
        waitingNextSentence: 'Waiting for your next sentence...',
        stopChat: 'Stop Chat',
        startContinuousChat: 'Start Continuous Chat',
        testingLLM: '🧪 Testing LLM Connection...',
        sendingTestRequest: 'Sending test request to LLM server...',
        testRequestFailed: '❌ Test request failed',
        testRequestFailedDetail: 'Unable to send test request, please check connection',
        ttsEnabled: '🔊 TTS Enabled',
        ttsDisabled: '🔇 TTS Disabled',
        ttsEnabledDetail: 'AI responses will be played as audio',
        ttsDisabledDetail: 'AI responses will be text only',
        uploadAudioFile: 'Please upload audio file...',

        // 流式识别相关
        streamRecognition: '🌊 Stream Recognition',
        preparing: 'Preparing...',
        waitingRecognitionResults: 'Waiting for recognition results...',
        aiReply: '🤖 AI Reply: ',
        aiMemory: '🧠 AI Memory: Empty',
        aiMemoryEmpty: 'Empty',
        aiMemoryActive: 'Active',
        userInfo: '👤 User ID: - | 🌐 Online: 0',
        userId: 'User ID',
        online: 'Online',

        // 聊天结果区域
        chatResultsPlaceholder: '<!-- Chat/Recognition results will be displayed here -->',

        // 语言切换
        currentLang: 'EN'
    }
};

// 当前语言
let currentLanguage = 'en'; // 默认英文

// 获取当前语言文本的辅助函数
function getLangText(key) {
    return LANGUAGE_DATA[currentLanguage] && LANGUAGE_DATA[currentLanguage][key] ?
           LANGUAGE_DATA[currentLanguage][key] :
           LANGUAGE_DATA['en'][key] || key;
}

// 语言切换功能
function toggleLanguage() {
    // 切换语言
    currentLanguage = currentLanguage === 'zh' ? 'en' : 'zh';

    // 保存到localStorage
    localStorage.setItem('preferredLanguage', currentLanguage);

    // 应用语言
    applyLanguage(currentLanguage);
}

// 应用语言设置
function applyLanguage(lang) {
    const langData = LANGUAGE_DATA[lang];

    if (!langData) {
        console.error('Language data not found for:', lang);
        return;
    }

    // 更新页面标题
    document.title = langData.pageTitle;

    // 更新主标题
    $('h1').text(langData.mainTitle);

    // 更新模式按钮
    $('#realtimeModeBtn').html(langData.realtimeMode);
    $('#offlineModeBtn').html(langData.offlineMode);

    // 更新控制按钮
    $('#streamBtn').text(langData.startChat);
    $('#resetBtn').text(langData.resetChat);
    $('#testBtn').text(langData.testLLM);
    $('#configBtn').text(langData.audioSettings);

    // 更新TTS按钮 - 根据当前状态显示对应文本
    const ttsBtn = $('#ttsBtn');
    if (appState && appState.ttsEnabled !== undefined) {
        ttsBtn.text(appState.ttsEnabled ? langData.ttsOn : langData.ttsOff);
    } else {
        ttsBtn.text(langData.ttsOn);
    }

    // 更新音频配置面板
    $('#audioConfigPanel h4').html(langData.audioConfig);
    $('label:contains("📊")').html(`${langData.sampleRate}<select id="sampleRateSelect" onchange="ConfigManager.updateAudioConfig()" style="margin-left: 10px; padding: 5px;"><option value="8000">8kHz (${langData.phoneQuality})</option><option value="16000" selected>16kHz (${langData.standardQuality})</option><option value="22050">22kHz (${langData.highQuality})</option></select>`);
    $('label:contains("⏱️")').html(`${langData.sendInterval}<select id="sendIntervalSelect" onchange="ConfigManager.updateAudioConfig()" style="margin-left: 10px; padding: 5px;"><option value="50">50ms (${langData.goodRealtime})</option><option value="100" selected>100ms (${langData.balanced})</option><option value="200">200ms (${langData.bandwidthSaving})</option></select>`);

    // 更新带宽信息显示
    ConfigManager.updateBandwidthInfo();

    // 更新音频设置按钮文本（如果面板是显示的）
    const $configBtn = $('#configBtn');
    const $audioPanel = $('#audioConfigPanel');
    if ($audioPanel.is(':visible')) {
        $configBtn.text(langData.hideSettings);
    } else {
        $configBtn.text(langData.audioSettings);
    }

    // 更新文件上传区域
    $('#offlineControls h4').text(langData.uploadAudioFile);
    $('#offlineControls p').html(langData.dragFilesHere);
    $('#offlineControls button[onclick*="audioFile"]').text(langData.selectFile);
    $('#offlineButtons button:first').html(langData.batchRecognition);
    $('#offlineButtons button:last').html(langData.streamRecognition);
    $('#offlineControls .upload-area > div:last-child').text(langData.fileFormatsSupport);

    // 更新状态信息 - 检查并更新各种可能的状态文本
    const $status = $('#status');
    const $currentText = $('#currentText');
    const currentStatusText = $status.text();
    const currentText = $currentText.text();
    const otherLang = currentLanguage === 'zh' ? 'en' : 'zh';
    const otherLangData = LANGUAGE_DATA[otherLang];

    // 更新状态文本
    if (currentStatusText === otherLangData.disconnected) {
        $status.text(langData.disconnected);
    } else if (currentStatusText === otherLangData.listening) {
        $status.text(langData.listening);
    } else if (currentStatusText === otherLangData.connectingWs) {
        $status.text(langData.connectingWs);
    } else if (currentStatusText === otherLangData.asrConnecting) {
        $status.text(langData.asrConnecting);
    } else if (currentStatusText === otherLangData.micStarting) {
        $status.text(langData.micStarting);
    } else if (currentStatusText === otherLangData.aiThinking) {
        $status.text(langData.aiThinking);
    } else if (currentStatusText === otherLangData.aiResponding) {
        $status.text(langData.aiResponding);
    } else if (currentStatusText === otherLangData.testingLLM) {
        $status.text(langData.testingLLM);
    } else if (currentStatusText === otherLangData.ttsEnabled) {
        $status.text(langData.ttsEnabled);
    } else if (currentStatusText === otherLangData.ttsDisabled) {
        $status.text(langData.ttsDisabled);
    }

    // 更新当前文本
    if (currentText === otherLangData.waitingOperation) {
        $currentText.text(langData.waitingOperation);
    } else if (currentText === otherLangData.waitingToStart) {
        $currentText.text(langData.waitingToStart);
    } else if (currentText === otherLangData.waitingNextSentence) {
        $currentText.text(langData.waitingNextSentence);
    } else if (currentText === otherLangData.sendingTestRequest) {
        $currentText.text(langData.sendingTestRequest);
    } else if (currentText === otherLangData.testRequestFailedDetail) {
        $currentText.text(langData.testRequestFailedDetail);
    } else if (currentText === otherLangData.ttsEnabledDetail) {
        $currentText.text(langData.ttsEnabledDetail);
    } else if (currentText === otherLangData.ttsDisabledDetail) {
        $currentText.text(langData.ttsDisabledDetail);
    } else if (currentText === otherLangData.uploadAudioFile) {
        $currentText.text(langData.uploadAudioFile);
    }

    // 更新AI记忆状态
    updateMemoryStatus();

    // 更新用户信息状态
    if (appState && appState.currentUserId) {
        updateUserInfo(appState.currentUserId, 0);
    } else {
        updateUserInfo(null, 0);
    }

    // 更新聊天结果区域注释（如果存在）
    const resultsComment = $('#results').html();
    if (resultsComment && resultsComment.includes('聊天/识别结果将显示在这里')) {
        $('#results').html(langData.chatResultsPlaceholder);
    }

    // 更新语言指示器
    $('#currentLang').text(langData.currentLang);

    // 更新常量中的状态文本
    updateStatusConstants(langData);
}

// 更新状态常量
function updateStatusConstants(langData) {
    CONSTANTS.STATUS_TEXT = {
        DISCONNECTED: langData.disconnected,
        CONNECTING: langData.connecting,
        ASR_CONNECTING: langData.asrConnecting,
        LISTENING: langData.listening,
        PROCESSING: langData.processing,
        AI_THINKING: langData.aiThinking,
        AI_RESPONDING: langData.aiResponding,
        MIC_STARTING: langData.micStarting,
        MIC_FAILED: langData.micFailed,
        CONNECTION_FAILED: langData.connectionFailed,
        CONNECTION_CLOSED: langData.connectionClosed
    };
}

// 初始化语言设置
function initializeLanguage() {
    // 从localStorage读取用户偏好，默认为英文
    const savedLanguage = localStorage.getItem('preferredLanguage') || 'en';
    currentLanguage = savedLanguage;

    // 应用语言设置
    applyLanguage(currentLanguage);
}

// 在页面加载完成后初始化语言
$(document).ready(function() {
    // 延迟初始化语言，确保所有元素都已加载
    setTimeout(function() {
        initializeLanguage();
    }, 25);
});



