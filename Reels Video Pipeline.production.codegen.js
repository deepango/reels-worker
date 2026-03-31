const webhook_Generate_Video = trigger({
  type: 'n8n-nodes-base.webhook',
  version: 2.1,
  config: { name: 'Webhook - Generate Video', parameters: { httpMethod: 'POST', path: 'generate-video', responseMode: 'onReceived', options: {} }, position: [200, 300] }
});

const postgres_Insert_Job = node({
  type: 'n8n-nodes-base.postgres',
  version: 2.6,
  config: { name: 'Postgres - Insert Job', parameters: { schema: { __rl: true, mode: 'list', value: 'public' }, table: { __rl: true, mode: 'name', value: 'video_jobs' }, columns: { mappingMode: 'defineBelow', value: { customer_id: expr('{{$json.body.customer_id}}'), topic: expr('{{$json.body.topic}}'), status: 'pending' }, matchingColumns: ['id'] }, options: { outputColumns: ['id', 'topic', 'customer_id', 'created_at'] } }, credentials: { postgres: { id: 'REPLACE_POSTGRES_CRED_ID', name: 'Postgres account' } }, position: [440, 300] }
});

const anthropic_Generate_Script = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.4,
  config: { name: 'Anthropic - Generate Script', parameters: { method: 'POST', url: 'https://api.anthropic.com/v1/messages', authentication: 'genericCredentialType', genericAuthType: 'httpHeaderAuth', sendBody: true, specifyBody: 'json', jsonBody: expr('{{ { "model": $env.ANTHROPIC_MODEL || "claude-3-5-haiku-20241022", "max_tokens": 2048, "messages": [ { "role": "user", "content": "Generate a short 5-scene real-estate reel script for topic: " + $("Webhook - Generate Video").item.json.body.topic + ". Return strict JSON object with key scenes (array). Each scene object: scene_id (number), image_prompt (string), voiceover_text (string)." } ] } }}'), options: { response: { response: { responseFormat: 'json' } } } }, credentials: { httpHeaderAuth: { id: 'REPLACE_ANTHROPIC_CRED_ID', name: 'anthropic' } }, position: [680, 300] }
});

const set_Script_Context = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: { name: 'Set - Script Context', parameters: { assignments: { assignments: [{ name: 'job_id', type: 'number', value: expr('{{$("Postgres - Insert Job").item.json.id}}') }, { name: 'topic', type: 'string', value: expr('{{$("Webhook - Generate Video").item.json.body.topic}}') }, { name: 'customer_id', type: 'number', value: expr('{{$("Webhook - Generate Video").item.json.body.customer_id}}') }, { name: 'scenes', type: 'array', value: expr('{{ JSON.parse($json.content[0].text.replace(/```json\\n?|\\n?```/g, \'\').trim()).scenes }}') }] } }, position: [920, 300] }
});

const split_Scenes = node({
  type: 'n8n-nodes-base.splitOut',
  version: 1,
  config: { name: 'Split - Scenes', parameters: { fieldToSplitOut: 'scenes', include: 'selectedOtherFields', fieldsToInclude: 'job_id,topic,customer_id' }, position: [1160, 300] }
});

const loop_Scenes = node({
  type: 'n8n-nodes-base.splitInBatches',
  version: 3,
  config: { name: 'Loop - Scenes', parameters: { batchSize: 1, options: {} }, position: [1380, 300] }
});

const aggregate_Final_Scenes = node({
  type: 'n8n-nodes-base.aggregate',
  version: 1,
  config: { name: 'Aggregate - Final Scenes', parameters: { aggregate: 'aggregateAllItemData', destinationFieldName: 'scenes', options: {} }, position: [4040, 420] }
});

const set_Queue_Payload = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: { name: 'Set - Queue Payload', parameters: { assignments: { assignments: [{ name: 'queue_payload', type: 'object', value: expr('{{ { job_id: $("Postgres - Insert Job").item.json.id, topic: $("Webhook - Generate Video").item.json.body.topic, customer_id: $("Webhook - Generate Video").item.json.body.customer_id, scenes: $json.scenes } }}') }] } }, position: [4260, 420] }
});

const redis_Push_Render_Job = node({
  type: 'n8n-nodes-base.redis',
  version: 1,
  config: { name: 'Redis - Push Render Job', parameters: { operation: 'push', list: 'reels:jobs', messageData: expr('{{ JSON.stringify($json.queue_payload) }}'), tail: true }, credentials: { redis: { id: 'REPLACE_REDIS_CRED_ID', name: 'Redis account' } }, position: [4480, 420] }
});

const postgres_Mark_Processing = node({
  type: 'n8n-nodes-base.postgres',
  version: 2.6,
  config: { name: 'Postgres - Mark Processing', parameters: { schema: { __rl: true, mode: 'list', value: 'public' }, table: { __rl: true, mode: 'name', value: 'video_jobs' }, columns: { mappingMode: 'defineBelow', value: { id: expr('{{$("Postgres - Insert Job").item.json.id}}'), status: 'processing', cost_breakdown: expr('{{ { anthropic: 0.01, replicate: 0.05, elevenlabs: 0.03 } }}'), updated_at: expr('{{$now}}') }, matchingColumns: ['id'] }, operation: 'update', options: {} }, credentials: { postgres: { id: 'REPLACE_POSTGRES_CRED_ID', name: 'Postgres account' } }, position: [4700, 420] }
});

const respond_Queued = node({
  type: 'n8n-nodes-base.respondToWebhook',
  version: 1.3,
  config: { name: 'Respond - Queued', parameters: { respondWith: 'json', responseBody: expr('{{ { ok: true, job_id: $("Postgres - Insert Job").item.json.id, status: \'queued\' } }}'), options: {} }, position: [4920, 420] }
});

const set_Scene_Context = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: { name: 'Set - Scene Context', parameters: { assignments: { assignments: [{ name: 'job_id', type: 'number', value: expr('{{$json.job_id}}') }, { name: 'topic', type: 'string', value: expr('{{$json.topic}}') }, { name: 'customer_id', type: 'number', value: expr('{{$json.customer_id}}') }, { name: 'scene_id', type: 'number', value: expr('{{$json.scenes.scene_id}}') }, { name: 'image_prompt', type: 'string', value: expr('{{$json.scenes.image_prompt}}') }, { name: 'voiceover_text', type: 'string', value: expr('{{$json.scenes.voiceover_text}}') }, { name: 'replicate_poll_count', type: 'number', value: 0 }, { name: 'replicate_max_polls', type: 'number', value: 40 }] } }, position: [1600, 300] }
});

const replicate_Create_Prediction = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.4,
  config: { name: 'Replicate - Create Prediction', parameters: { method: 'POST', url: 'https://api.replicate.com/v1/predictions', authentication: 'genericCredentialType', genericAuthType: 'httpHeaderAuth', sendBody: true, specifyBody: 'json', jsonBody: expr('{{ { "version": "39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b", "input": { "prompt": $json.image_prompt, "width": 1024, "height": 576 } } }}'), options: {} }, credentials: { httpHeaderAuth: { id: 'REPLACE_REPLICATE_CRED_ID', name: 'replicate' } }, position: [1840, 300] }
});

const set_Replicate_Meta = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: { name: 'Set - Replicate Meta', parameters: { assignments: { assignments: [{ name: 'job_id', type: 'number', value: expr('{{$("Set - Scene Context").item.json.job_id}}') }, { name: 'topic', type: 'string', value: expr('{{$("Set - Scene Context").item.json.topic}}') }, { name: 'scene_id', type: 'number', value: expr('{{$("Set - Scene Context").item.json.scene_id}}') }, { name: 'voiceover_text', type: 'string', value: expr('{{$("Set - Scene Context").item.json.voiceover_text}}') }, { name: 'prediction_id', type: 'string', value: expr('{{$json.id}}') }, { name: 'prediction_get_url', type: 'string', value: expr('{{$json.urls.get}}') }, { name: 'replicate_poll_count', type: 'number', value: 0 }, { name: 'replicate_max_polls', type: 'number', value: 40 }] } }, position: [2060, 300] }
});

const wait_Replicate_Poll_Interval = node({
  type: 'n8n-nodes-base.wait',
  version: 1.1,
  config: { name: 'Wait - Replicate Poll Interval', parameters: { amount: 3 }, position: [2280, 300] }
});

const replicate_Get_Prediction = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.4,
  config: { name: 'Replicate - Get Prediction', parameters: { url: expr('{{$json.prediction_get_url}}'), authentication: 'genericCredentialType', genericAuthType: 'httpHeaderAuth', options: {} }, credentials: { httpHeaderAuth: { id: 'REPLACE_REPLICATE_CRED_ID', name: 'replicate' } }, position: [2500, 300] }
});

const iF_Replicate_Succeeded = node({
  type: 'n8n-nodes-base.if',
  version: 2,
  config: { name: 'IF - Replicate Succeeded', parameters: { conditions: { string: [{ value1: expr('{{$json.status}}'), operation: 'equal', value2: 'succeeded' }] } }, position: [2720, 300] }
});

const set_Image_Output = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: { name: 'Set - Image Output', parameters: { assignments: { assignments: [{ name: 'job_id', type: 'number', value: expr('{{$("Set - Replicate Meta").item.json.job_id}}') }, { name: 'topic', type: 'string', value: expr('{{$("Set - Replicate Meta").item.json.topic}}') }, { name: 'scene_id', type: 'number', value: expr('{{$("Set - Replicate Meta").item.json.scene_id}}') }, { name: 'voiceover_text', type: 'string', value: expr('{{$("Set - Replicate Meta").item.json.voiceover_text}}') }, { name: 'image_url', type: 'string', value: expr('{{$json.output[0]}}') }] } }, position: [2940, 180] }
});

const merge_Scene_Assets_by_scene_id = merge({
  version: 3.2,
  config: { name: 'Merge - Scene Assets by scene_id', parameters: { mode: 'combine', combineBy: 'combineByFields', fieldsToMatchString: 'scene_id', options: {} }, position: [4260, 280] }
});

const crypto_Hash_Voiceover = node({
  type: 'n8n-nodes-base.crypto',
  version: 1,
  config: { name: 'Crypto - Hash Voiceover', parameters: { action: 'hash', type: 'MD5', value: expr('{{$json.voiceover_text}}'), dataPropertyName: 'voiceover_hash' }, position: [3160, 180] }
});

const s3_Check_Cache = node({
  type: 'n8n-nodes-base.s3',
  version: 1,
  config: { name: 'S3 - Check Cache', parameters: { operation: 'download', bucketName: expr('{{$env.B2_BUCKET_NAME}}'), fileName: expr('{{"tts-cache/" + $env.ELEVENLABS_VOICE_ID + "/" + $("Crypto - Hash Voiceover").item.json.voiceover_hash + ".mpga"}}') }, credentials: { s3: { id: 'REPLACE_S3_B2_CRED_ID', name: 'Backblaze B2 (S3)' } }, onError: 'continueErrorOutput', position: [3380, 180] }
});

const if_Cache_Exists = node({
  type: 'n8n-nodes-base.if',
  version: 2,
  config: { name: 'IF - Cache Exists', parameters: { conditions: { string: [{ value1: expr('{{$json.error}}'), operation: 'isEmpty' }] } }, position: [3600, 180] }
});

const elevenLabs_Generate_Audio = node({
  type: 'n8n-nodes-base.httpRequest',
  version: 4.4,
  config: { name: 'ElevenLabs - Generate Audio', parameters: { method: 'POST', url: expr('{{ "https://api.elevenlabs.io/v1/text-to-speech/" + $env.ELEVENLABS_VOICE_ID }}'), authentication: 'genericCredentialType', genericAuthType: 'httpHeaderAuth', sendBody: true, specifyBody: 'json', jsonBody: expr('{{ { "text": $("Set - Image Output").item.json.voiceover_text, "model_id": "eleven_multilingual_v2" } }}'), options: { response: { response: { responseFormat: 'file', outputPropertyName: 'audio_data' } } } }, credentials: { httpHeaderAuth: { id: 'REPLACE_ELEVENLABS_CRED_ID', name: 'elevenlabs' } }, position: [3820, 300] }
});

const s3_Upload_Audio_B2 = node({
  type: 'n8n-nodes-base.s3',
  version: 1,
  config: { name: 'S3 - Upload Audio (B2)', parameters: { operation: 'upload', bucketName: expr('{{$env.B2_BUCKET_NAME}}'), binaryData: true, binaryPropertyName: 'audio_data', fileName: expr('{{"tts-cache/" + $env.ELEVENLABS_VOICE_ID + "/" + $("Crypto - Hash Voiceover").item.json.voiceover_hash + ".mpga"}}'), additionalFields: {} }, credentials: { s3: { id: 'REPLACE_S3_B2_CRED_ID', name: 'Backblaze B2 (S3)' } }, position: [4040, 300] }
});

const set_Audio_Output = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: { name: 'Set - Audio Output', parameters: { assignments: { assignments: [{ name: 'job_id', type: 'number', value: expr('{{$("Set - Image Output").item.json.job_id}}') }, { name: 'topic', type: 'string', value: expr('{{$("Set - Image Output").item.json.topic}}') }, { name: 'scene_id', type: 'number', value: expr('{{$("Set - Image Output").item.json.scene_id}}') }, { name: 'image_url', type: 'string', value: expr('{{$("Set - Image Output").item.json.image_url}}') }, { name: 'audio_url', type: 'string', value: expr('{{$env.B2_ENDPOINT + "/" + $env.B2_BUCKET_NAME + "/tts-cache/" + $env.ELEVENLABS_VOICE_ID + "/" + $("Crypto - Hash Voiceover").item.json.voiceover_hash + ".mpga"}}') }] } }, position: [4040, 180] }
});

const iF_Replicate_Continue_Polling = node({
  type: 'n8n-nodes-base.if',
  version: 2,
  config: { name: 'IF - Replicate Continue Polling', parameters: { conditions: { number: [{ value1: expr('{{ $("Set - Replicate Meta").item.json.replicate_poll_count + 1 }}'), operation: 'smaller', value2: expr('{{ $("Set - Replicate Meta").item.json.replicate_max_polls }}') }] } }, position: [2940, 460] }
});

const set_Replicate_Next_Poll = node({
  type: 'n8n-nodes-base.set',
  version: 3.4,
  config: { name: 'Set - Replicate Next Poll', parameters: { assignments: { assignments: [{ name: 'job_id', type: 'number', value: expr('{{$("Set - Replicate Meta").item.json.job_id}}') }, { name: 'topic', type: 'string', value: expr('{{$("Set - Replicate Meta").item.json.topic}}') }, { name: 'scene_id', type: 'number', value: expr('{{$("Set - Replicate Meta").item.json.scene_id}}') }, { name: 'voiceover_text', type: 'string', value: expr('{{$("Set - Replicate Meta").item.json.voiceover_text}}') }, { name: 'prediction_get_url', type: 'string', value: expr('{{$("Set - Replicate Meta").item.json.prediction_get_url}}') }, { name: 'replicate_poll_count', type: 'number', value: expr('{{$("Set - Replicate Meta").item.json.replicate_poll_count + 1}}') }, { name: 'replicate_max_polls', type: 'number', value: expr('{{$("Set - Replicate Meta").item.json.replicate_max_polls}}') }] } }, position: [3160, 460] }
});

const postgres_Mark_Failed = node({
  type: 'n8n-nodes-base.postgres',
  version: 2.6,
  config: { name: 'Postgres - Mark Failed', parameters: { schema: { __rl: true, mode: 'list', value: 'public' }, table: { __rl: true, mode: 'name', value: 'video_jobs' }, columns: { mappingMode: 'defineBelow', value: { id: expr('{{$("Postgres - Insert Job").item.json.id}}'), status: 'failed', error_logs: expr('{{$json.error || $json.message || \'scene processing failed\'}}'), updated_at: expr('{{$now}}') }, matchingColumns: ['id'] }, operation: 'update', options: {} }, credentials: { postgres: { id: 'REPLACE_POSTGRES_CRED_ID', name: 'Postgres account' } }, position: [3380, 600] }
});

const respond_Failed = node({
  type: 'n8n-nodes-base.respondToWebhook',
  version: 1.3,
  config: { name: 'Respond - Failed', parameters: { respondWith: 'json', responseCode: 500, responseBody: expr('{{ { ok: false, error: \'Workflow failed\', job_id: $("Postgres - Insert Job").item.json.id } }}'), options: {} }, position: [3600, 600] }
});

const wf = workflow('', 'Reels Video Pipeline (Production)', { executionOrder: 'v1', saveExecutionProgress: true, saveManualExecutions: true });

export default wf
  .add(webhook_Generate_Video)
  .to(postgres_Insert_Job)
  .to(anthropic_Generate_Script)
  .to(set_Script_Context)
  .to(split_Scenes)
  .to(splitInBatches(loop_Scenes)
  .onEachBatch(set_Scene_Context
    .to(replicate_Create_Prediction)
    .to(set_Replicate_Meta)
    .to(wait_Replicate_Poll_Interval)
    .to(replicate_Get_Prediction)
    .to(iF_Replicate_Succeeded.onTrue(set_Image_Output
      .to(crypto_Hash_Voiceover)
      .to(s3_Check_Cache)
      .to(if_Cache_Exists
        .onTrue(set_Audio_Output)
        .onFalse(elevenLabs_Generate_Audio
          .to(s3_Upload_Audio_B2)
          .to(set_Audio_Output)))).onFalse(iF_Replicate_Continue_Polling.onTrue(set_Replicate_Next_Poll
        .to(wait_Replicate_Poll_Interval)).onFalse(postgres_Mark_Failed
        .to(respond_Failed)))))
  .onDone(aggregate_Final_Scenes
    .to(set_Queue_Payload)
    .to(redis_Push_Render_Job)
    .to(postgres_Mark_Processing)
    .to(respond_Queued)))
  .add(set_Image_Output.to(merge_Scene_Assets_by_scene_id.input(0)))
  .add(set_Audio_Output.to(merge_Scene_Assets_by_scene_id.input(1)))
  .add(merge_Scene_Assets_by_scene_id)
  .to(loop_Scenes)