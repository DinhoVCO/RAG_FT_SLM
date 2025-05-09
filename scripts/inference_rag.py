import sys
#sys.path.append('../src')
from vector_stores.faiss import VectorStoreFaiss
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
from sentence_transformers import SentenceTransformer
from rag.rag_basic import RAGPipeline
import pandas as pd
import argparse
from utils.datasets_splits import load_dataset_splits
from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed
from peft import PeftModel, PeftConfig

def load_model_auto(device="cuda", model_name=None, lora_adapter_path=None):
    if lora_adapter_path:
        print(f"Loading base model from LoRA adapter: {lora_adapter_path}")
        config = PeftConfig.from_pretrained(lora_adapter_path)
        model = AutoModelForCausalLM.from_pretrained(
            config.base_model_name_or_path,
            torch_dtype="auto",
            trust_remote_code=True,
        ).to(device)
        model = PeftModel.from_pretrained(model, lora_adapter_path).to(device)
        model = model.merge_and_unload()
        tokenizer = AutoTokenizer.from_pretrained(config.base_model_name_or_path, trust_remote_code=True, padding_side="left")
    elif model_name:
        print(f"Loading base model: {model_name}")
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype="auto",
            trust_remote_code=True,
        ).to(device)
        tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True, padding_side="left")
    else:
        raise ValueError("You must provide either a model_name or a lora_adapter_path.")

    tokenizer.pad_token = tokenizer.eos_token

    return model, tokenizer

def create_rag_pipeline(dataset_name, reader_model, tokenizer, vector_store, max_new_tokens=None):
    reader_llm = pipeline(
        model=reader_model,
        tokenizer=tokenizer,
        task="text-generation",
        do_sample=False,
        return_full_text=False,
        max_new_tokens=max_new_tokens
    )
    # output = generator(
    #     prompt,
    #     max_new_tokens=120,
    #     num_beams=5,
    #     early_stopping=True,
    #     length_penalty=0.1,
    #     do_sample=False,  # Desactiva sampling, activa búsqueda determinista
    #     return_full_text=False 
    # )
    return RAGPipeline(dataset_name, llm=reader_llm, knowledge_index=vector_store)

def load_dataset_for_inference(dataset_name):
    train_ds, val_ds, test_ds = load_dataset_splits(dataset_name)
    if dataset_name=='clapnq':
        test_ds = test_ds.rename_column("input", "question")
    print(test_ds)
    return test_ds

def save_answers_to_csv(inference, file_path):
    df = pd.DataFrame({
        'inference': inference,
    })
    df.to_csv(file_path, index=False)

def main(gen_model, lora_adapter_path, max_new_tokens, use_rag, vector_store_path, dataset_name, output_csv_path, bs_emb, bs_gen, top_k):
    set_seed(42)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")
    reader_model, tokenizer = load_model_auto(device, gen_model, lora_adapter_path)
    vector_store = None
    if(use_rag):
        vector_store = VectorStoreFaiss.load_local(vector_store_path)
    rag_pipeline = create_rag_pipeline(dataset_name,reader_model, tokenizer, vector_store, max_new_tokens)
    dataset = load_dataset_for_inference(dataset_name)
    answers = rag_pipeline.answer_batch(dataset, bs_emb, bs_gen, top_k)
    print("inference completed")
    olny_answers = [item[0]['generated_text'] for item in answers]
    save_answers_to_csv(olny_answers, output_csv_path)
    print(f"answer save in {output_csv_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG Pipeline Execution")
    parser.add_argument("--gen_model", type=str, default=None, help="Name of the generative model")
    parser.add_argument("--lora_adapter_path", type=str, default=None, help="Name or path of the peft model")
    parser.add_argument("--max_new_tokens", type=int, default=None, help="Max new tokens")
    parser.add_argument('--use_rag', action='store_true', help='Use RAG?')
    parser.add_argument("--vector_store_path", type=str, required=False, help="Path to the FAISS vector store")
    parser.add_argument("--dataset_name", type=str, required=True, help="Name of dataset (covid, clapnq, boolq, teleqna)")
    parser.add_argument("--output_csv_path", type=str, required=True, help="Path to save the answer output CSV")
    parser.add_argument("--bs_emb", type=int, default=100, help="batch size for encode dataset")
    parser.add_argument("--bs_gen", type=int, default=20, help="batch size for the LLM model")
    parser.add_argument("--top_k", type=int, default=10, help="Number of documents to retrieve from the index")

    args = parser.parse_args()

    main(
        args.gen_model,
        args.lora_adapter_path,
        args.max_new_tokens,
        args.use_rag,
        args.vector_store_path,
        args.dataset_name,
        args.output_csv_path,
        args.bs_emb,
        args.bs_gen,
        args.top_k,
    )