import streamlit as st
from openai import OpenAI
import pandas as pd
from datetime import datetime, timedelta
# from st_supabase_connection import SupabaseConnection
from supabase import create_client, Client
from pulp import LpProblem, LpBinary, LpVariable, lpSum, LpMaximize

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
sessions_data = response_sessions.data # bringing in the sessions data for olympic events

df_sessions = pd.DataFrame(sessions_data)

st.dataframe(df_sessions)


# Assuming 'df' is the dataframe you fetched from Supabase
def flatten_prices(df):
    # Columns that stay as they are
    id_vars = [
        'Sport', 'Venue', 'Zone', 'Session Code', 'Date', 
        'Games Day', 'Session Type', 'Session Description', 
        'Start Time', 'End Time'
    ]
    
    # The price columns you want to turn into rows
    value_vars = ['Category A', 'Category B', 'Category C', 'Category D', 
                  'Category E', 'Category F', 'Category G', 'Category H', 
                  'Category I', 'Category J']

    # Unpivot the table
    df_long = pd.melt(
        df, 
        id_vars=id_vars, 
        value_vars=value_vars,
        var_name='Price Category', 
        value_name='Price'
    )
    
    # Clean up: Remove rows where Price might be null (if a category isn't offered)
    df_long = df_long.dropna(subset=['Price'])
    
    return df_long

df_new = flatten_prices(pd.DataFrame(raw_data))
st.dataframe(df_new)

# 3. Optimization Setup
prob = LpProblem("Olympic_Planning", LpMaximize)

# Create a binary variable for each event (1 if we go, 0 if not)
event_vars = LpVariable.dicts("Event", [e['Session Code'] for e in sessions_data], cat=LpBinary)

# Objective: Maximize number of events attended
prob += lpSum([event_vars[e['Session Code']] for e in sessions_data])

# --- CONSTRAINTS ---

# Constraint 1: Max 24 tickets
prob += lpSum([event_vars[e['Session Code']] for e in sessions_data]) <= 24

# Constraint 2: Budget (Example: $200000)
budget = st.sidebar.number_input("Total Budget ($)", value=200000)
prob += lpSum([event_vars[e['id']] * e['price'] for e in sessions_data]) <= budget

# Constraint 3: No Overlaps (The Time-Slot Constraint)
# We group events by day and check for overlapping hours
for day in range(1, 6): # Days 1 to 5
    day_events = [e for e in sessions_data if e['day'] == day]
    for i, e1 in enumerate(day_events):
        for e2 in day_events[i+1:]:
            # If times overlap, we can't pick both
            if (e1['start_time'] < e2['end_time'] and e2['start_time'] < e1['end_time']):
                prob += event_vars[e1['id']] + event_vars[e2['id']] <= 1

# 4. Solve and Display
if st.button("Optimize My Schedule"):
    prob.solve()
    selected_ids = [e_id for e_id in event_vars if event_vars[e_id].varValue == 1]
    
    st.success(f"Optimized! You can attend {len(selected_ids)} events.")
    # Display results in a table
    itinerary = [e for e in sessions_data if e['id'] in selected_ids]
    st.table(itinerary)
