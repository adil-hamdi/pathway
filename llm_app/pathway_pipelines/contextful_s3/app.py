"""
Microservice for  a context-aware ChatGPT assistant.

The following program reads in a collection of documents from a public AWS S3 bucket,
embeds each document using the OpenAI document embedding model,
then builds an index for fast retrieval of documents relevant to a question,
effectively replacing a vector database.

The program then starts a REST API endpoint serving queries about programming in Pathway.

Each query text is first turned into a vector using OpenAI embedding service,
then relevant documentation pages are found using a Nearest Neighbor index computed
for documents in the corpus. A prompt is build from the relevant documentations pages
and sent to the OpenAI GPT-4 chat service for processing.

Usage:
In llm_app/ run:
python main.py --mode contextful_s3

To call the REST API:
curl --data '{"user": "user", "query": "How to connect to Kafka in Pathway?"}' http://localhost:8080/ | jq
"""
import pathway as pw
from llm_app.config import Config
from llm_app.model_wrappers import OpenAIChatGPTModel, OpenAIEmbeddingModel
from pathway.stdlib.ml.index import KNNIndex


class DocumentInputSchema(pw.Schema):
    doc: str


class QueryInputSchema(pw.Schema):
    query: str
    user: str


def run(config: Config):
    embedder = OpenAIEmbeddingModel(api_key=config.api_key)

    documents = pw.io.s3.read(
        "llm_demo/data/",
        aws_s3_settings=pw.io.s3.AwsS3Settings(
            bucket_name="pathway-examples",
            region="eu-central-1",
        ),
        format="json",
        schema=DocumentInputSchema,
        mode="streaming",
    )

    enriched_documents = documents + documents.select(
        data=embedder.apply(text=pw.this.doc, locator=config.embedder_locator)
    )

    index = KNNIndex(enriched_documents, d=config.embedding_dimension)

    query, response_writer = pw.io.http.rest_connector(
        host=config.rest_host,
        port=config.rest_port,
        schema=QueryInputSchema,
        autocommit_duration_ms=50,
    )

    query += query.select(
        data=embedder.apply(text=pw.this.query, locator=config.embedder_locator),
    )

    query_context = index.query(query, k=3).select(
        pw.this.query, documents_list=pw.this.result
    )

    @pw.udf
    def build_prompt(documents, query):
        docs_str = "\n".join(documents)
        prompt = f"Given the following documents : \n {docs_str} \nanswer this query: {query}"
        return prompt

    prompt = query_context.select(
        prompt=build_prompt(pw.this.documents_list, pw.this.query)
    )

    model = OpenAIChatGPTModel(api_key=config.api_key)

    responses = prompt.select(
        query_id=pw.this.id,
        result=model.apply(
            pw.this.prompt,
            locator=config.model_locator,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        ),
    )

    response_writer(responses)

    pw.run()
