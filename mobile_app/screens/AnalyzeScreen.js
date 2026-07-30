import { getApiUrl } from "../config";
import React, { useState, useEffect } from 'react';
import { View, Text, Button, Image, ActivityIndicator, ScrollView, Alert, TextInput, TouchableOpacity } from 'react-native';
import * as ImagePicker from 'expo-image-picker';

export default function AnalyzeScreen({ route, navigation }) {
  const { user, token, recordId } = route.params || {};
  const [imageUri, setImageUri] = useState(null);
  const [imageBase64, setImageBase64] = useState(null);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [plotImage, setPlotImage] = useState(null);

  // Form inputs for saving
  const [patientId, setPatientId] = useState('');
  const [ecgDatetime, setEcgDatetime] = useState('');
  const [rootCause, setRootCause] = useState('');
  const [rootCauseTime, setRootCauseTime] = useState('');
  const [saveStatus, setSaveStatus] = useState(null);

  // Load record if recordId parameter passed from RecordsScreen
  useEffect(() => {
    if (recordId) {
      loadRecordById(recordId);
    }
  }, [recordId]);

  const loadRecordById = async (id) => {
    setLoading(true);
    try {
      const resp = await fetch(`${getApiUrl()}/record/${id}`);
      if (resp.ok) {
        const data = await resp.json();
        setPatientId(data.patient_id || '');
        setEcgDatetime(data.ecg_datetime || '');
        setRootCause(data.root_cause || '');
        setRootCauseTime(data.root_cause_time || '');
        // Set a placeholder imageUri so the UI shows the record is loaded
        const waveformUrl = `${getApiUrl()}/record/${id}/waveform`;
        setImageUri(waveformUrl);
        if (data.analysis) {
          setResult(data.analysis);
          fetchPlotImage(data.analysis);
        }
      } else {
        Alert.alert("Error", `Failed to load record #${id}`);
      }
    } catch (e) {
      Alert.alert("Error", `Network error: ${e.message}`);
    }
    setLoading(false);
  };

  const pickImageFromGallery = async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      Alert.alert("Permission Required", "Media library access permission is required to select an image.");
      return;
    }
    const res = await ImagePicker.launchImageLibraryAsync({ base64: true, quality: 0.8 });
    if (!res.canceled && res.assets && res.assets.length > 0) {
      setImageUri(res.assets[0].uri);
      setImageBase64(res.assets[0].base64 || null);
      setResult(null);
      setPlotImage(null);
    }
  };

  const takePhotoWithCamera = async () => {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) {
      Alert.alert("Permission Required", "Camera permission is required to capture an ECG photo.");
      return;
    }
    const res = await ImagePicker.launchCameraAsync({ base64: true, quality: 0.8 });
    if (!res.canceled && res.assets && res.assets.length > 0) {
      setImageUri(res.assets[0].uri);
      setImageBase64(res.assets[0].base64 || null);
      setResult(null);
      setPlotImage(null);
    }
  };

  const fetchPlotImage = async (analysisData) => {
    try {
      const resp = await fetch(`${getApiUrl()}/analysis/plot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(analysisData)
      });
      if (resp.ok) {
        const j = await resp.json();
        if (j.plot_base64) {
          setPlotImage('data:image/png;base64,' + j.plot_base64);
        }
      }
    } catch (_) {}
  };

  const analyze = async () => {
    if (!imageUri) return;
    setLoading(true);
    setResult(null);
    setPlotImage(null);

    try {
      let b64Data = imageBase64;
      if (!b64Data && imageUri.startsWith('data:')) {
        b64Data = imageUri.split(',')[1];
      }

      const headers = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const payload = {
        image_base64: b64Data || '',
        pixels_per_mm: 20.0,
        prominence: 0.5,
      };

      const resp = await fetch(`${getApiUrl()}/analyze`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      });

      if (!resp.ok) {
        let errText = `Status ${resp.status}`;
        try {
          const errJson = await resp.json();
          if (errJson.detail) errText = errJson.detail;
        } catch (_) {}
        setResult({ error: `Analysis failed: ${errText}` });
        setLoading(false);
        return;
      }
      const json = await resp.json();
      setResult(json);
      fetchPlotImage(json);
    } catch (e) {
      setResult({ error: `Network error connecting to "${getApiUrl()}". Details: ${e.message}` });
    }
    setLoading(false);
  };

  const saveRecord = async () => {
    if (!result) {
      Alert.alert("Notice", "Please analyze an ECG image before saving.");
      return;
    }
    setSaveStatus("Saving...");
    try {
      const form = new FormData();
      form.append('metadata', JSON.stringify({
        patient_id: patientId || 'Anonymous',
        ecg_datetime: ecgDatetime || new Date().toISOString(),
        root_cause: rootCause,
        root_cause_time: rootCauseTime,
      }));
      form.append('pixels_per_mm', '20.0');
      form.append('prominence', '0.5');

      if (imageBase64) {
        form.append('file', {
          uri: imageUri,
          type: 'image/png',
          name: 'ecg_upload.png',
        });
      }

      const resp = await fetch(`${getApiUrl()}/save_record`, {
        method: 'POST',
        body: form,
      });

      if (resp.ok) {
        const j = await resp.json();
        setSaveStatus(`Saved successfully (Record ID #${j.record_id})`);
        Alert.alert("Success", `Record saved with ID #${j.record_id}`);
      } else {
        setSaveStatus("Failed to save record.");
      }
    } catch (e) {
      setSaveStatus(`Save error: ${e.message}`);
    }
  };

  const metrics = result?.metrics || {};

  return (
    <ScrollView style={{ flex: 1, padding: 16, backgroundColor: '#fff' }}>
      {/* Top Header Navigation Tabs */}
      <View style={{ flexDirection: 'row', backgroundColor: '#e9ecef', borderRadius: 8, padding: 4, marginBottom: 16 }}>
        <TouchableOpacity style={{ flex: 1, paddingVertical: 8, backgroundColor: '#007bff', borderRadius: 6, alignItems: 'center' }}>
          <Text style={{ color: '#fff', fontWeight: 'bold' }}>Analyze</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={() => navigation.navigate('Records', { user, token })} style={{ flex: 1, paddingVertical: 8, alignItems: 'center' }}>
          <Text style={{ color: '#495057', fontWeight: '600' }}>Records</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={() => navigation.navigate('Compare', { user, token })} style={{ flex: 1, paddingVertical: 8, alignItems: 'center' }}>
          <Text style={{ color: '#495057', fontWeight: '600' }}>Compare</Text>
        </TouchableOpacity>
        {user?.role === 'Administrator' && (
          <TouchableOpacity onPress={() => navigation.navigate('AdminAnalysis', { user, token })} style={{ flex: 1, paddingVertical: 8, alignItems: 'center' }}>
            <Text style={{ color: '#495057', fontWeight: '600' }}>Admin</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity onPress={() => navigation.replace('Login')} style={{ paddingHorizontal: 8, paddingVertical: 8, backgroundColor: '#dc3545', borderRadius: 6, marginLeft: 4 }}>
          <Text style={{ color: '#fff', fontWeight: 'bold', fontSize: 11 }}>Logout</Text>
        </TouchableOpacity>
      </View>

      {user && (
        <Text style={{ marginBottom: 12, fontSize: 13, color: '#6c757d' }}>
          User: <Text style={{ fontWeight: 'bold', color: '#212529' }}>{user.username}</Text> ({user.role})
        </Text>
      )}

      {/* Image Picker Section */}
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 12 }}>
        <TouchableOpacity onPress={pickImageFromGallery} style={{ flex: 1, marginRight: 6, backgroundColor: '#007bff', padding: 10, borderRadius: 6, alignItems: 'center' }}>
          <Text style={{ color: '#fff', fontWeight: 'bold' }}>Pick Gallery</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={takePhotoWithCamera} style={{ flex: 1, marginLeft: 6, backgroundColor: '#28a745', padding: 10, borderRadius: 6, alignItems: 'center' }}>
          <Text style={{ color: '#fff', fontWeight: 'bold' }}>Take Photo</Text>
        </TouchableOpacity>
      </View>

      {imageUri && (
        <View style={{ alignItems: 'center', marginVertical: 8 }}>
          <Image source={{ uri: imageUri }} style={{ width: '100%', height: 220, borderRadius: 8, resizeMode: 'contain', backgroundColor: '#f8f9fa' }} />
        </View>
      )}

      {imageUri && (
        <TouchableOpacity onPress={analyze} style={{ backgroundColor: '#17a2b8', padding: 12, borderRadius: 6, alignItems: 'center', marginVertical: 8 }}>
          <Text style={{ color: '#fff', fontWeight: 'bold', fontSize: 16 }}>Analyze ECG Image</Text>
        </TouchableOpacity>
      )}

      {loading && <ActivityIndicator size="large" color="#007bff" style={{ marginVertical: 16 }} />}

      {/* Analysis Result Formatting */}
      {result && (
        <View style={{ marginTop: 12, padding: 14, backgroundColor: '#f8f9fa', borderRadius: 8, borderWidth: 1, borderColor: '#dee2e6' }}>
          {result.error ? (
            <Text style={{ color: '#dc3545', fontWeight: 'bold' }}>{result.error}</Text>
          ) : (
            <View>
              <Text style={{ fontSize: 16, fontWeight: 'bold', color: '#212529', marginBottom: 8 }}>Clinical Metrics Result</Text>
              
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between' }}>
                <View style={{ width: '48%', backgroundColor: '#fff', padding: 8, borderRadius: 6, marginBottom: 8, borderWidth: 1, borderColor: '#e9ecef' }}>
                  <Text style={{ fontSize: 11, color: '#6c757d' }}>Heart Rate</Text>
                  <Text style={{ fontSize: 16, fontWeight: 'bold', color: '#007bff' }}>
                    {metrics.heart_rate_bpm ? `${metrics.heart_rate_bpm.toFixed(1)} bpm` : 'N/A'}
                  </Text>
                </View>
                <View style={{ width: '48%', backgroundColor: '#fff', padding: 8, borderRadius: 6, marginBottom: 8, borderWidth: 1, borderColor: '#e9ecef' }}>
                  <Text style={{ fontSize: 11, color: '#6c757d' }}>PR Interval</Text>
                  <Text style={{ fontSize: 16, fontWeight: 'bold', color: '#28a745' }}>
                    {metrics.pr_interval_ms ? `${metrics.pr_interval_ms.toFixed(1)} ms` : 'N/A'}
                  </Text>
                </View>
                <View style={{ width: '48%', backgroundColor: '#fff', padding: 8, borderRadius: 6, marginBottom: 8, borderWidth: 1, borderColor: '#e9ecef' }}>
                  <Text style={{ fontSize: 11, color: '#6c757d' }}>QRS Duration</Text>
                  <Text style={{ fontSize: 16, fontWeight: 'bold', color: '#ffc107' }}>
                    {metrics.qrs_duration_ms ? `${metrics.qrs_duration_ms.toFixed(1)} ms` : 'N/A'}
                  </Text>
                </View>
                <View style={{ width: '48%', backgroundColor: '#fff', padding: 8, borderRadius: 6, marginBottom: 8, borderWidth: 1, borderColor: '#e9ecef' }}>
                  <Text style={{ fontSize: 11, color: '#6c757d' }}>QT Interval</Text>
                  <Text style={{ fontSize: 16, fontWeight: 'bold', color: '#17a2b8' }}>
                    {metrics.qt_interval_ms ? `${metrics.qt_interval_ms.toFixed(1)} ms` : 'N/A'}
                  </Text>
                </View>
              </View>

              {/* Display Generated Waveform Plot if available */}
              {plotImage && (
                <View style={{ marginTop: 12, alignItems: 'center' }}>
                  <Text style={{ fontWeight: '600', marginBottom: 4 }}>Waveform Digitization</Text>
                  <Image source={{ uri: plotImage }} style={{ width: '100%', height: 200, borderRadius: 6, resizeMode: 'contain' }} />
                </View>
              )}

              {/* Save Record Form */}
              <View style={{ marginTop: 16, paddingTop: 12, borderTopWidth: 1, borderTopColor: '#dee2e6' }}>
                <Text style={{ fontWeight: 'bold', marginBottom: 8 }}>Save Record to Database</Text>
                
                <TextInput placeholder="Patient ID (e.g. P-101)" value={patientId} onChangeText={setPatientId} style={{ borderWidth: 1, borderColor: '#ccc', borderRadius: 4, padding: 8, marginBottom: 8 }} />
                <TextInput placeholder="ECG Datetime (e.g. 2026-07-24 10:00)" value={ecgDatetime} onChangeText={setEcgDatetime} style={{ borderWidth: 1, borderColor: '#ccc', borderRadius: 4, padding: 8, marginBottom: 8 }} />
                <TextInput placeholder="Possible Root Cause" value={rootCause} onChangeText={setRootCause} style={{ borderWidth: 1, borderColor: '#ccc', borderRadius: 4, padding: 8, marginBottom: 8 }} />
                <TextInput placeholder="Root Cause Time (e.g. 14:30)" value={rootCauseTime} onChangeText={setRootCauseTime} style={{ borderWidth: 1, borderColor: '#ccc', borderRadius: 4, padding: 8, marginBottom: 8 }} />
                
                <TouchableOpacity onPress={saveRecord} style={{ backgroundColor: '#6c757d', padding: 10, borderRadius: 6, alignItems: 'center', marginTop: 4 }}>
                  <Text style={{ color: '#fff', fontWeight: 'bold' }}>Save Record</Text>
                </TouchableOpacity>

                {saveStatus && <Text style={{ marginTop: 6, color: '#28a745', fontWeight: '500' }}>{saveStatus}</Text>}
              </View>
            </View>
          )}
        </View>
      )}

      <View style={{ height: 40 }} />
    </ScrollView>
  );
}
