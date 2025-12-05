## per-commit (NVIDIA) vs per-commit-amd (AMD)
| Suite | Total | Common | Only in NVIDIA | Only in AMD |
| --- | --- | --- | --- | --- |
| unit-test-backend-1-gpu | 102 vs 68 | 64 | 38 | 4 |

| Common | Only in NVIDIA | Only in AMD |
| --- | --- | --- |
| hicache/test_hicache.py<br>hicache/test_hicache_mla.py<br>hicache/test_hicache_storage.py<br>lora/test_lora.py<br>lora/test_lora_backend.py<br>lora/test_lora_cuda_graph.py<br>lora/test_lora_eviction.py<br>lora/test_lora_qwen3.py<br>lora/test_multi_lora_backend.py<br>models/test_compressed_tensors_models.py<br>models/test_embedding_models.py<br>models/test_qwen_models.py<br>models/test_reward_models.py<br>models/test_transformers_models.py<br>openai_server/basic/test_openai_embedding.py<br>openai_server/basic/test_openai_server.py<br>openai_server/basic/test_protocol.py<br>openai_server/basic/test_serving_chat.py<br>openai_server/basic/test_serving_completions.py<br>openai_server/basic/test_serving_embedding.py<br>openai_server/features/test_enable_thinking.py<br>openai_server/features/test_json_constrained.py<br>openai_server/features/test_json_mode.py<br>openai_server/features/test_openai_server_ebnf.py<br>openai_server/features/test_reasoning_content.py<br>openai_server/function_call/test_openai_function_calling.py<br>openai_server/function_call/test_tool_choice.py<br>openai_server/validation/test_large_max_new_tokens.py<br>openai_server/validation/test_matched_stop.py<br>openai_server/validation/test_openai_server_ignore_eos.py<br>openai_server/validation/test_request_length_validation.py<br>quant/test_block_int8.py<br>rl/test_update_weights_from_disk.py<br>test_abort.py<br>test_chunked_prefill.py<br>test_create_kvindices.py<br>test_ebnf_constrained.py<br>test_eval_fp8_accuracy.py<br>test_function_call_parser.py<br>test_fused_moe.py<br>test_gpt_oss_1gpu.py<br>test_input_embeddings.py<br>test_io_struct.py<br>test_jinja_template_utils.py<br>test_metrics.py<br>test_metrics_utils.py<br>test_mla.py<br>test_mla_deepseek_v3.py<br>test_no_chunked_prefill.py<br>test_page_size.py<br>test_penalty.py<br>test_pytorch_sampling_backend.py<br>test_radix_attention.py<br>test_reasoning_parser.py<br>test_regex_constrained.py<br>test_retract_decode.py<br>test_server_args.py<br>test_skip_tokenizer_init.py<br>test_srt_endpoint.py<br>test_srt_engine.py<br>test_torch_compile.py<br>test_torch_compile_moe.py<br>test_torch_native_attention_backend.py<br>test_triton_attention_backend.py | lora/test_chunked_sgmv_backend.py<br>lora/test_lora_radix_cache.py<br>lora/test_lora_update.py<br>models/test_cross_encoder_models.py<br>models/test_encoder_embedding_models.py<br>models/test_generation_models.py<br>models/test_vlm_models.py<br>openai_server/features/test_openai_server_hidden_states.py<br>quant/test_fp8_kernel.py<br>quant/test_int8_kernel.py<br>quant/test_triton_scaled_mm.py<br>quant/test_w8a8_quantization.py<br>rl/test_update_weights_from_tensor.py<br>test_eagle_infer_a.py<br>test_eagle_infer_b.py<br>test_fa3.py<br>test_harmony_parser.py<br>test_hidden_states.py<br>test_hybrid_attn_backend.py<br>test_logprobs.py<br>test_mla_fp8.py<br>test_mla_int8_deepseek_v3.py<br>test_multi_tokenizer.py<br>test_no_overlap_scheduler.py<br>test_original_logprobs.py<br>test_priority_scheduling.py<br>test_request_queue_validation.py<br>test_standalone_speculative_decoding.py<br>test_start_profile.py<br>test_torchao.py<br>test_triton_attention_kernels.py<br>test_triton_moe_channel_fp8_kernel.py<br>test_triton_sliding_window.py<br>test_utils_update_weights.py<br>test_vision_chunked_prefill.py<br>test_vision_openai_server_a.py<br>test_vision_openai_server_b.py<br>test_vlm_input_format.py | quant/test_awq_dequant.py<br>test_rope_rocm.py<br>test_wave_attention_backend.py<br>test_wave_attention_kernels.py |

## per-commit-2-gpu (NVIDIA) vs per-commit-2-gpu-amd (AMD)
| Suite | Total | Common | Only in NVIDIA | Only in AMD |
| --- | --- | --- | --- | --- |
| unit-test-backend-2-gpu | 9 vs 5 | 5 | 4 | 0 |

| Common | Only in NVIDIA | Only in AMD |
| --- | --- | --- |
| lora/test_lora_tp.py<br>rl/test_update_weights_from_distributed.py<br>test_data_parallelism.py<br>test_load_weights_from_remote_instance.py<br>test_patch_torch.py | hicache/test_hicache_storage_3fs_backend.py<br>hicache/test_hicache_storage_file_backend.py<br>test_dp_attention.py<br>test_release_memory_occupation.py |  |

## per-commit-4-gpu (NVIDIA) vs per-commit-4-gpu-amd (AMD)
| Suite | Total | Common | Only in NVIDIA | Only in AMD |
| --- | --- | --- | --- | --- |
| unit-test-backend-4-gpu | 5 vs 1 | 1 | 4 | 0 |

| Common | Only in NVIDIA | Only in AMD |
| --- | --- | --- |
| test_pp_single_node.py | models/test_qwen3_next_models.py<br>test_gpt_oss_4gpu.py<br>test_local_attn.py<br>test_multi_instance_release_memory_occupation.py |  |

## per-commit-8-gpu (NVIDIA) vs per-commit-8-gpu-amd (AMD)
| Suite | Total | Common | Only in NVIDIA | Only in AMD |
| --- | --- | --- | --- | --- |
| unit-test-backend-8-gpu | 5 vs 1 | 1 | 4 | 0 |

| Common | Only in NVIDIA | Only in AMD |
| --- | --- | --- |
| test_full_deepseek_v3.py | lora/test_lora_llama4.py<br>test_disaggregation.py<br>test_disaggregation_different_tp.py<br>test_disaggregation_pp.py |  |

