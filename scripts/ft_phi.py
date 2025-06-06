import os
import sys
import argparse
from datasets import load_dataset
from peft import LoraConfig
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
)
from trl import SFTTrainer, DataCollatorForCompletionOnlyLM
from utils.data_for_train_phi import get_dataset_for_train_phi
from dotenv import load_dotenv
import wandb
from vector_stores.faiss import VectorStoreFaiss
from sentence_transformers import SentenceTransformer
import torch
from peft import get_peft_model
from transformers import set_seed

#5e 10bs

def parse_args():
    parser = argparse.ArgumentParser(description="Fine-tune Phi-2 model with LoRA.")
    parser.add_argument('--batch_size', type=int, required=True, help='Batch size for training')
    parser.add_argument('--new_model_name', type=str, default='phi-2-3GPP-RAG-ft' , help='Name for the fine-tuned model')
    parser.add_argument('--dataset_name', type=str, required=True, help='Name for the dataset for training( covid, teleqna, coolq')
    parser.add_argument('--include_docs', action='store_true', help='Use RAG?')
    parser.add_argument('--top_k', type=int, required=True, help='Use retrieval top-k documents')
    parser.add_argument("--vector_store_path", type=str, default=None, help="Path to the FAISS vector store")
    parser.add_argument('--save_path', type=str, required=True, help='Path to save the fine-tuned model')
    parser.add_argument('--num_epochs', type=int, required=True, help='Number of training epochs')
    args = parser.parse_args()
    return args

def load_model_and_tokenizer(model_name):
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        device_map="auto",
    )
    model.config.use_cache = False
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    tokenizer.pad_token = tokenizer.unk_token  # use unk rather than eos token to prevent endless generation
    tokenizer.pad_token_id = tokenizer.convert_tokens_to_ids(tokenizer.pad_token)
    tokenizer.padding_side = 'right'

    return model, tokenizer

def configure_lora():
    return LoraConfig(
        r=32,
        lora_alpha=32,
        target_modules=["Wqkv", "fc1", "fc2"],
        bias="none",
        lora_dropout=0.05,
        task_type="CAUSAL_LM",
    )

def configure_training_arguments(output_dir, batch_size, num_epochs):
    return TrainingArguments(
        output_dir=output_dir,
        num_train_epochs=num_epochs,
        fp16=True,
        bf16=False,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        eval_strategy="steps",                             # ← eval cada época
        save_strategy="steps",                             # ← guardar checkpoint cada época
        load_best_model_at_end=True,                       # ← guarda el mejor
        metric_for_best_model="eval_loss",                 # ← evalúa usando pérdida
        greater_is_better=False,                           # ← pérdida más baja es mejor
        disable_tqdm=True,
        gradient_accumulation_steps=4,
        gradient_checkpointing=True,
        max_grad_norm=0.3,
        learning_rate=2e-4,
        weight_decay=0.001,
        optim="paged_adamw_32bit",
        lr_scheduler_type="cosine",
        max_steps=-1,
        warmup_ratio=0.03,
        group_by_length=True,
        save_steps=100,
        save_total_limit=2,
        logging_steps=25,
        report_to="wandb",
    )

def train_model(batch_size, model_name, new_model_name, save_path, num_epochs,train_dataset_name,include_docs,top_k, vector_store_path):
    if(include_docs):
        vector_store = VectorStoreFaiss.load_local(vector_store_path)
    else:
        vector_store=None
    print(f"Using k = {top_k} passages")
    train_ds, val_ds = get_dataset_for_train_phi(train_dataset_name, include_docs, vector_store,top_k, 8)
    print("Input Example:")
    print(train_ds[0]['text'])
    print("Loading model")
    model, tokenizer = load_model_and_tokenizer(model_name)
    peft_config = configure_lora()
    output_dir = os.path.join(save_path, new_model_name)
    training_arguments = configure_training_arguments(
       output_dir, batch_size, num_epochs
    )
    print("Training")
    trainer = SFTTrainer(
        model=model,
        train_dataset=train_ds,
        eval_dataset=val_ds, 
        args=training_arguments,
        peft_config=peft_config,
    )
    checkpoints = [d for d in os.listdir(output_dir) if d.startswith("checkpoint-")] if os.path.exists(output_dir) else []
    if checkpoints:
        print("🔵 Checkpoints detected. Resuming training from the last one.")
        print(checkpoints)
        trainer.train(resume_from_checkpoint=True)
    else:
        print("🟢 No checkpoints found. Starting training from scratch.")
        trainer.train()
    trainer.model.save_pretrained(os.path.join(save_path, "best_"+new_model_name))
    trainable_params = sum(p.numel() for p in trainer.model.parameters() if p.requires_grad)
    print(f"trainable parameters: {trainable_params}")

def main():
    args = parse_args()
    set_seed(42)
    load_dotenv()
    wandb.login() 
    wandb.init(
        project="SBBD_phi-2-adapters",
        name=args.new_model_name,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Usando dispositivo: {device}")
    train_model(
        batch_size=args.batch_size,
        model_name='microsoft/phi-2',
        new_model_name=args.new_model_name,
        save_path=args.save_path,
        num_epochs=args.num_epochs,
        train_dataset_name=args.dataset_name,
        include_docs = args.include_docs,
        top_k =args.top_k,
        vector_store_path = args.vector_store_path
    )

if __name__ == "__main__":
    main()