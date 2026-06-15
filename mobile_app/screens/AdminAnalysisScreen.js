import React, { useEffect, useState } from 'react';
import { View, Text, Button, FlatList, Image, TouchableOpacity } from 'react-native';

export default function AdminAnalysisScreen({ route }) {
  const [tables, setTables] = useState([]);
  const [rows, setRows] = useState([]);
  const fetchTables = async () => {
    const resp = await fetch('http://10.0.2.2:8000/tables', { headers: { Authorization: `Bearer ${route.params?.token}` } });
    const json = await resp.json();
    setTables(json.tables || []);
  };
  const fetchTable = async (t) => {
    const resp = await fetch(`http://10.0.2.2:8000/table/${t}`, { headers: { Authorization: `Bearer ${route.params?.token}` } });
    const json = await resp.json();
    setRows(json.rows || []);
  };
  useEffect(() => { fetchTables(); }, []);
  return (
    <View style={{ padding: 16 }}>
      <Button title="Refresh tables" onPress={fetchTables} />
      <FlatList data={tables} keyExtractor={(t) => t} renderItem={({ item }) => (
        <TouchableOpacity onPress={() => fetchTable(item)}>
          <View style={{ padding: 8, borderBottomWidth: 1 }}><Text>{item}</Text></View>
        </TouchableOpacity>
      )} />
      <Text>Rows preview:</Text>
      <FlatList data={rows} keyExtractor={(r, i) => String(i)} renderItem={({ item }) => (
        <View style={{ padding: 8, borderBottomWidth: 1 }}><Text>{JSON.stringify(item)}</Text></View>
      )} />
    </View>
  );
}
