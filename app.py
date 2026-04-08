import streamlit as st
from openai import OpenAI
import pandas as pd
from datetime import datetime, timedelta
# from st_supabase_connection import SupabaseConnection
from supabase import create_client, Client


# 1. Initialize the Supabase Client
@st.cache_resource
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_connection()

# 2. Function to Query Data
# We use st.cache_data to prevent hitting the DB on every user chat toggle
@st.cache_data(ttl=600) # Cache for 10 minutes
def run_query():
    # .select("*") fetches all columns; .execute() returns the response object
    return supabase.table("prices").select("*").execute()

@st.cache_data(ttl=600) # Cache for 10 minutes
def run_query_sessions():
    # .select("*") fetches all columns; .execute() returns the response object
    return supabase.table("sessions").select("*").execute()

response_sessions = run_query_sessions()
sessions_data = response_sessions.data # bringing in the last few meals 

df_sessions = pd.DataFrame(sessions_data)

st.dataframe(df_sessions)
